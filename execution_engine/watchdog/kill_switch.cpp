/**
 * AYVID Kill Switch — Memory-mapped VaR watchdog + independent IB flatten.
 *
 * Runs outside the Python GIL at 10Hz. Reads /tmp/ayvid_risk.mmap and,
 * on risk breach, does two things IN PARALLEL:
 *
 *   1. SIGTERM → SIGKILL the Python trading process (so it can't emit
 *      any more orders).
 *   2. Directly call the Interactive Brokers Client Portal REST API
 *      to (a) GET /iserver/accounts/{accountId}/orders and cancel every
 *      working order, then (b) GET /iserver/accounts/{accountId}/positions
 *      and submit OPPOSITE MARKET orders to flatten every open position.
 *
 * This is the step-2 flatten the PDF calls for: a broker-side disaster
 * (Python alive but mis-behaving, bracket stops cancelled, drawdown blows
 * the threshold) is handled by an independent C++ path that does NOT
 * share any state with Python. The only shared surface is the mmap risk
 * state the kill switch reads and an auth cookie it writes / refreshes
 * out-of-band via the Python-side login session.
 *
 * Build:
 *   g++ -O2 -std=c++17 -o kill_switch kill_switch.cpp -lcurl -lpthread
 *
 * Runtime env (read via getenv, fail-soft — if absent, we still SIGKILL
 * Python but skip the REST flatten, logging CRITICAL):
 *
 *   AYVID_IB_GATEWAY_URL     default "https://localhost:5000/v1/api"
 *                            Client Portal Gateway base URL.
 *   AYVID_IB_ACCOUNT_ID      required for REST flatten
 *   AYVID_IB_CAFILE          optional — path to a PEM bundle for the
 *                            Client Portal self-signed cert. When unset,
 *                            we disable peer verification (matches the
 *                            default self-signed install; this is the
 *                            same posture as IB's official web login).
 *   AYVID_IB_SESSION_COOKIE  optional — full cookie header written by the
 *                            Python side after /sso/validate. When set we
 *                            send it as a Cookie: header; otherwise we
 *                            rely on the Gateway's persistent session.
 */

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <mutex>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
#include <vector>

#ifdef __has_include
#  if __has_include(<curl/curl.h>)
#    include <curl/curl.h>
#    define AYVID_HAVE_CURL 1
#  else
#    define AYVID_HAVE_CURL 0
#  endif
#else
#  include <curl/curl.h>
#  define AYVID_HAVE_CURL 1
#endif

/* ──────────────────────────────────────────────────────────────────
 * Shared-memory risk frame.
 *
 * The Python writer (`execution_engine/watchdog/mmap_bridge.py`) lays out
 * the file as a classic seqlock frame:
 *
 *   [ uint64 seq_begin ][ RiskStatePayload (72B) ][ uint64 seq_end ]
 *
 * Writer protocol:
 *   1. write frame with seq_begin = ODD  (write-in-progress marker)
 *   2. flush
 *   3. overwrite seq_begin and seq_end with the SAME even counter
 *
 * Reader contract (this process):
 *   - load-acquire seq_begin  → if odd, a writer is mid-update, retry
 *   - memcpy the payload
 *   - acquire fence
 *   - load-acquire seq_end    → if != seq_begin, we raced a writer, retry
 *
 * The struct layout MUST match `_PAYLOAD_FORMAT = "=5d q 2i 2d"` +
 * leading/trailing `=Q` in mmap_bridge.py. `#pragma pack(1)` removes
 * alignment padding so the binary sizes are fixed at 72 and 88 bytes.
 * The static_asserts below will fail the build if either side drifts.
 * ────────────────────────────────────────────────────────────────── */
/* Only the payload is byte-packed (matches Python's "=" native/no-pad
 * struct layout). The outer frame is left unpacked so that the two
 * `uint64_t` seqlock counters keep their natural 8-byte alignment —
 * otherwise `__atomic_load_n` on them would warn about a misaligned
 * atomic op. The packed payload has alignment 1, so the compiler
 * inserts zero padding between the leading counter and the payload,
 * and the trailing counter lands at offset 80 (already 8-aligned).
 */
#pragma pack(push, 1)
struct RiskStatePayload {
    double   drawdown_pct;
    double   var_95;
    double   var_99;
    double   portfolio_equity;
    double   peak_equity;
    int64_t  timestamp_us;
    int32_t  python_pid;
    int32_t  armed;
    double   max_drawdown_pct;
    double   max_var_pct;
};
#pragma pack(pop)

struct RiskStateFrame {
    uint64_t         seq_begin;
    RiskStatePayload payload;
    uint64_t         seq_end;
};

static_assert(sizeof(RiskStatePayload) == 72,
    "RiskStatePayload must be 72 bytes — drift from Python _PAYLOAD_FORMAT");
static_assert(sizeof(RiskStateFrame) == 88,
    "RiskStateFrame must be 88 bytes — drift from Python _STRUCT_SIZE");
static_assert(offsetof(RiskStateFrame, seq_begin) == 0,
    "seq_begin must sit at offset 0 — Python writes it first");
static_assert(offsetof(RiskStateFrame, payload)   == 8,
    "payload must sit at offset 8 — right after the leading seq counter");
static_assert(offsetof(RiskStateFrame, seq_end)   == 80,
    "seq_end must sit at offset 80 — right after the 72-byte payload");

static constexpr size_t RISK_FRAME_SIZE = sizeof(RiskStateFrame);

static std::atomic<bool> g_running{true};
static FILE* g_logfile = nullptr;
static std::mutex g_log_mtx;

/* Acquire-ordered seqlock read with bounded retries.
 * Returns true iff a non-torn frame was captured into `out`.
 *
 * `max_retries` is sized for our access pattern: writer runs at 10Hz
 * (100ms between frames), reader runs at 10Hz on the same tick, and a
 * single write is on the order of microseconds. 16 retries with a
 * yield between each is ~0.5ms of backoff — orders of magnitude more
 * headroom than we need but cheap if we're unlucky. */
static bool read_risk_state_seqlock(
    const RiskStateFrame* frame,
    RiskStatePayload& out,
    int max_retries = 16
) {
    for (int i = 0; i < max_retries; ++i) {
        uint64_t s1 = __atomic_load_n(&frame->seq_begin, __ATOMIC_ACQUIRE);
        if (s1 & 1ULL) {
            std::this_thread::yield();
            continue;
        }
        /* Snapshot payload between the two acquire loads. The leading
         * acquire above and the trailing acquire below prevent the
         * compiler (and CPU) from reordering the payload reads outside
         * this window. */
        std::memcpy(&out, &frame->payload, sizeof(RiskStatePayload));
        std::atomic_thread_fence(std::memory_order_acquire);
        uint64_t s2 = __atomic_load_n(&frame->seq_end, __ATOMIC_ACQUIRE);
        if (s1 == s2 && (s2 & 1ULL) == 0) {
            return true;
        }
        std::this_thread::yield();
    }
    return false;
}

static void signal_handler(int sig) {
    g_running.store(false, std::memory_order_relaxed);
    fprintf(stderr, "\n[WATCHDOG] caught signal %d, shutting down\n", sig);
}

static void log_msg(const char* level, const char* fmt, ...) {
    auto now = std::chrono::system_clock::now();
    std::time_t t = std::chrono::system_clock::to_time_t(now);
    struct tm tm_buf{};
#if defined(__APPLE__) || defined(__linux__)
    localtime_r(&t, &tm_buf);
#else
    tm_buf = *localtime(&t);
#endif
    char ts[64];
    std::strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", &tm_buf);

    char msg[2048];
    std::va_list args;
    va_start(args, fmt);
    std::vsnprintf(msg, sizeof(msg), fmt, args);
    va_end(args);

    std::lock_guard<std::mutex> lk(g_log_mtx);
    std::fprintf(stderr, "[%s] [WATCHDOG] [%s] %s\n", ts, level, msg);
    std::fflush(stderr);
    if (g_logfile) {
        std::fprintf(g_logfile, "[%s] [WATCHDOG] [%s] %s\n", ts, level, msg);
        std::fflush(g_logfile);
    }
}

static bool process_alive(pid_t pid) {
    return kill(pid, 0) == 0;
}

static void terminate_process(pid_t pid) {
    if (pid <= 0) return;

    log_msg("CRITICAL", "sending SIGTERM to PID %d", pid);
    kill(pid, SIGTERM);

    for (int i = 0; i < 20 && process_alive(pid); ++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(100));

    if (process_alive(pid)) {
        log_msg("CRITICAL", "process %d still alive after 2s — sending SIGKILL", pid);
        kill(pid, SIGKILL);
    }
}

/* ──────────────────────────────────────────────────────────────────
 * IB Client Portal REST flatten
 *
 * Minimum viable flow (works against CPAPI v1 2024+ endpoints):
 *
 *   1. POST /iserver/auth/status        → assert session is live
 *   2. GET  /iserver/account            → confirm accountId
 *   3. GET  /iserver/accounts/{a}/orders?filters=filled,cancelled,inactive
 *                                       → list working orders, cancel each
 *   4. POST /iserver/account/{a}/order/{id}/cancel   (loop)
 *   5. GET  /portfolio/{a}/positions/0  → list positions
 *   6. POST /iserver/account/{a}/orders → submit SELL/BUY MKT per position
 *
 * Every call is bounded by a 5s timeout. On any failure we keep going:
 * partial flatten is better than no flatten.
 * ────────────────────────────────────────────────────────────────── */

struct HttpResponse {
    long status_code = 0;
    std::string body;
};

#if AYVID_HAVE_CURL
static size_t _curl_write_cb(void* contents, size_t size, size_t nmemb, void* user) {
    size_t n = size * nmemb;
    static_cast<std::string*>(user)->append(static_cast<char*>(contents), n);
    return n;
}

static HttpResponse http_request(
    const std::string& method,
    const std::string& url,
    const std::string& body,
    const std::vector<std::string>& extra_headers,
    const char* cafile,
    long timeout_ms
) {
    HttpResponse out;
    CURL* c = curl_easy_init();
    if (!c) {
        log_msg("ERROR", "curl_easy_init failed");
        return out;
    }

    curl_easy_setopt(c, CURLOPT_URL, url.c_str());
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, _curl_write_cb);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, &out.body);
    curl_easy_setopt(c, CURLOPT_TIMEOUT_MS, timeout_ms);
    curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT_MS, (long)2000);
    curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(c, CURLOPT_TCP_KEEPALIVE, 1L);

    if (cafile && std::strlen(cafile) > 0) {
        curl_easy_setopt(c, CURLOPT_CAINFO, cafile);
        curl_easy_setopt(c, CURLOPT_SSL_VERIFYPEER, 1L);
    } else {
        // IB Client Portal Gateway ships with a self-signed cert by
        // default. Mirror the official IB webclient posture: skip
        // verify unless operator has provided a CA bundle.
        curl_easy_setopt(c, CURLOPT_SSL_VERIFYPEER, 0L);
        curl_easy_setopt(c, CURLOPT_SSL_VERIFYHOST, 0L);
    }

    struct curl_slist* hdr = nullptr;
    hdr = curl_slist_append(hdr, "Accept: application/json");
    hdr = curl_slist_append(hdr, "Content-Type: application/json");
    hdr = curl_slist_append(hdr, "User-Agent: ayvid-watchdog/1.0");
    for (const auto& h : extra_headers) {
        hdr = curl_slist_append(hdr, h.c_str());
    }
    curl_easy_setopt(c, CURLOPT_HTTPHEADER, hdr);

    if (method == "POST") {
        curl_easy_setopt(c, CURLOPT_POST, 1L);
        curl_easy_setopt(c, CURLOPT_POSTFIELDS, body.c_str());
        curl_easy_setopt(c, CURLOPT_POSTFIELDSIZE, (long)body.size());
    } else if (method != "GET") {
        curl_easy_setopt(c, CURLOPT_CUSTOMREQUEST, method.c_str());
        if (!body.empty()) {
            curl_easy_setopt(c, CURLOPT_POSTFIELDS, body.c_str());
            curl_easy_setopt(c, CURLOPT_POSTFIELDSIZE, (long)body.size());
        }
    }

    CURLcode rc = curl_easy_perform(c);
    if (rc != CURLE_OK) {
        log_msg("ERROR", "%s %s -> %s", method.c_str(), url.c_str(), curl_easy_strerror(rc));
    } else {
        curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &out.status_code);
    }
    curl_slist_free_all(hdr);
    curl_easy_cleanup(c);
    return out;
}

/* Extremely small JSON scalar extractor — enough for the few fields we
 * need from Client Portal payloads (orderId, conid, position, etc.)
 * without dragging in a full JSON library. Not a general parser; it
 * finds `"key":value` pairs at any depth. */
static std::string json_get_string(const std::string& json, const std::string& key) {
    std::string needle = "\"" + key + "\"";
    size_t pos = 0;
    while ((pos = json.find(needle, pos)) != std::string::npos) {
        size_t colon = json.find(':', pos + needle.size());
        if (colon == std::string::npos) return "";
        size_t p = colon + 1;
        while (p < json.size() && std::isspace(static_cast<unsigned char>(json[p]))) ++p;
        if (p >= json.size()) return "";
        if (json[p] == '"') {
            size_t end = json.find('"', p + 1);
            if (end == std::string::npos) return "";
            return json.substr(p + 1, end - p - 1);
        } else {
            size_t end = p;
            while (end < json.size() && (std::isdigit(static_cast<unsigned char>(json[end]))
                || json[end] == '.' || json[end] == '-' || json[end] == 'e' || json[end] == '+'))
                ++end;
            return json.substr(p, end - p);
        }
        pos = colon + 1;
    }
    return "";
}

static std::vector<std::string> json_scan_objects(const std::string& json, const std::string& array_key) {
    /* Locate `"array_key":[ ... ]` and return one string per top-level
     * {...} object inside the array. Bracket-depth aware. */
    std::vector<std::string> out;
    std::string needle = "\"" + array_key + "\"";
    size_t pos = json.find(needle);
    if (pos == std::string::npos) {
        // Fall back to "the outer value is already the array"
        size_t first = json.find('[');
        if (first == std::string::npos) return out;
        pos = first;
    } else {
        size_t bracket = json.find('[', pos);
        if (bracket == std::string::npos) return out;
        pos = bracket;
    }
    int depth = 0;
    size_t start = std::string::npos;
    for (size_t i = pos; i < json.size(); ++i) {
        char c = json[i];
        if (c == '[') { depth++; continue; }
        if (c == ']') { depth--; if (depth == 0) break; }
        if (depth == 1) {
            if (c == '{') {
                if (start == std::string::npos) start = i;
                int obrace = 1; size_t j = i + 1;
                while (j < json.size() && obrace > 0) {
                    if (json[j] == '{') obrace++;
                    else if (json[j] == '}') obrace--;
                    ++j;
                }
                if (obrace == 0) {
                    out.push_back(json.substr(i, j - i));
                    i = j - 1;
                    start = std::string::npos;
                }
            }
        }
    }
    return out;
}

static void ib_rest_flatten() {
    const char* gw_url  = std::getenv("AYVID_IB_GATEWAY_URL");
    const char* acct_id = std::getenv("AYVID_IB_ACCOUNT_ID");
    const char* cafile  = std::getenv("AYVID_IB_CAFILE");
    const char* cookie  = std::getenv("AYVID_IB_SESSION_COOKIE");

    if (!gw_url || std::strlen(gw_url) == 0) gw_url = "https://localhost:5000/v1/api";
    if (!acct_id || std::strlen(acct_id) == 0) {
        log_msg("CRITICAL",
            "AYVID_IB_ACCOUNT_ID not set — SKIPPING REST flatten (Python SIGKILL only). "
            "Set it in the launchd plist or systemd unit.");
        return;
    }

    std::vector<std::string> headers;
    if (cookie && std::strlen(cookie) > 0) {
        headers.push_back(std::string("Cookie: ") + cookie);
    }

    const long TIMEOUT_MS = 5000;

    // 1. Confirm session.
    {
        HttpResponse r = http_request(
            "POST",
            std::string(gw_url) + "/iserver/auth/status",
            "{}", headers, cafile, TIMEOUT_MS);
        if (r.status_code != 200) {
            log_msg("ERROR",
                "Client Portal auth check failed (HTTP %ld body=%.256s). "
                "Continuing anyway — any request that needs auth will 401.",
                r.status_code, r.body.c_str());
        } else {
            log_msg("INFO", "Client Portal session live");
        }
    }

    // 2. Fetch open orders; cancel every working one.
    {
        HttpResponse r = http_request(
            "GET",
            std::string(gw_url) + "/iserver/account/orders",
            "", headers, cafile, TIMEOUT_MS);
        if (r.status_code == 200) {
            auto orders = json_scan_objects(r.body, "orders");
            log_msg("CRITICAL", "Found %zu live orders; cancelling", orders.size());
            for (const auto& o : orders) {
                std::string oid = json_get_string(o, "orderId");
                std::string status = json_get_string(o, "status");
                if (oid.empty()) continue;
                if (status == "Filled" || status == "Cancelled" || status == "Inactive") continue;

                HttpResponse c = http_request(
                    "DELETE",
                    std::string(gw_url) + "/iserver/account/" + acct_id + "/order/" + oid,
                    "", headers, cafile, TIMEOUT_MS);
                log_msg("CRITICAL", "cancel order %s -> HTTP %ld", oid.c_str(), c.status_code);
            }
        } else {
            log_msg("ERROR",
                "GET /iserver/account/orders failed (HTTP %ld body=%.256s)",
                r.status_code, r.body.c_str());
        }
    }

    // 3. Fetch positions; submit opposite-side MKT for each.
    {
        HttpResponse r = http_request(
            "GET",
            std::string(gw_url) + "/portfolio/" + acct_id + "/positions/0",
            "", headers, cafile, TIMEOUT_MS);
        if (r.status_code == 200) {
            /* CPAPI returns a bare array here, not a keyed object. */
            auto positions = json_scan_objects(r.body, "");
            log_msg("CRITICAL", "Found %zu positions; flattening via MKT", positions.size());

            /* Batch payload (IB supports multi-order submit in one call). */
            std::string payload = "{\"orders\":[";
            bool any = false;
            for (const auto& p : positions) {
                std::string conid = json_get_string(p, "conid");
                std::string pos_s = json_get_string(p, "position");
                if (conid.empty() || pos_s.empty()) continue;
                double pos = std::strtod(pos_s.c_str(), nullptr);
                if (pos == 0.0) continue;

                const char* side = pos > 0 ? "SELL" : "BUY";
                long qty = static_cast<long>(std::fabs(pos));
                if (qty <= 0) continue;

                if (any) payload += ",";
                payload += "{\"conid\":" + conid
                        + ",\"orderType\":\"MKT\""
                        + ",\"side\":\"" + side + "\""
                        + ",\"tif\":\"IOC\""
                        + ",\"quantity\":" + std::to_string(qty)
                        + "}";
                any = true;
                log_msg("CRITICAL",
                    "flatten queued — conid=%s side=%s qty=%ld (from pos=%.4f)",
                    conid.c_str(), side, qty, pos);
            }
            payload += "]}";

            if (any) {
                HttpResponse s = http_request(
                    "POST",
                    std::string(gw_url) + "/iserver/account/" + acct_id + "/orders",
                    payload, headers, cafile, TIMEOUT_MS);
                log_msg("CRITICAL",
                    "flatten submit -> HTTP %ld body=%.512s",
                    s.status_code, s.body.c_str());

                /* CPAPI often returns a confirmation prompt; reply "true". */
                std::string reply_id = json_get_string(s.body, "id");
                if (!reply_id.empty()) {
                    HttpResponse rep = http_request(
                        "POST",
                        std::string(gw_url) + "/iserver/reply/" + reply_id,
                        "{\"confirmed\":true}", headers, cafile, TIMEOUT_MS);
                    log_msg("CRITICAL",
                        "flatten confirm reply -> HTTP %ld body=%.256s",
                        rep.status_code, rep.body.c_str());
                }
            } else {
                log_msg("INFO", "No non-zero positions — book already flat");
            }
        } else {
            log_msg("ERROR",
                "GET /portfolio/%s/positions/0 failed (HTTP %ld body=%.256s)",
                acct_id, r.status_code, r.body.c_str());
        }
    }
}
#else
static void ib_rest_flatten() {
    log_msg("ERROR",
        "Built WITHOUT libcurl — SKIPPING REST flatten. Rebuild with "
        "-lcurl so step-2 broker-side flatten is available.");
}
#endif

/* Open & mmap the risk frame read-only. On success, *out_fd and *out_map
 * hold the resources the caller must release. Returns 0 on success,
 * non-zero otherwise (already logged). */
static int open_risk_frame(const char* mmap_path, int* out_fd, void** out_map) {
    int fd = open(mmap_path, O_RDONLY);
    if (fd < 0) {
        log_msg("ERROR", "cannot open %s: %s", mmap_path, std::strerror(errno));
        return 1;
    }
    struct stat st{};
    if (fstat(fd, &st) < 0 || static_cast<size_t>(st.st_size) < RISK_FRAME_SIZE) {
        log_msg("ERROR",
            "mmap file too small (%ld bytes, need %zu for RiskStateFrame)",
            (long)st.st_size, RISK_FRAME_SIZE);
        close(fd);
        return 1;
    }
    void* mapped = mmap(nullptr, RISK_FRAME_SIZE, PROT_READ, MAP_SHARED, fd, 0);
    if (mapped == MAP_FAILED) {
        log_msg("ERROR", "mmap failed: %s", std::strerror(errno));
        close(fd);
        return 1;
    }
    *out_fd = fd;
    *out_map = mapped;
    return 0;
}

/* One-shot reader used by `tests/chaos/test_resilience.py` to validate
 * the producer/consumer binary contract. Prints the seqlock-clean
 * payload as a single-line JSON record to stdout and exits 0, or exits
 * 2 if the reader cannot get a clean read within `max_retries`. */
static int dump_once(const char* mmap_path) {
    int fd = -1;
    void* mapped = nullptr;
    if (open_risk_frame(mmap_path, &fd, &mapped) != 0) {
        return 1;
    }
    const RiskStateFrame* frame = static_cast<const RiskStateFrame*>(mapped);
    RiskStatePayload p{};
    bool ok = read_risk_state_seqlock(frame, p, /*max_retries=*/256);
    munmap(mapped, RISK_FRAME_SIZE);
    close(fd);
    if (!ok) {
        std::fprintf(stderr,
            "[dump_once] could not capture a clean seqlock frame from %s\n",
            mmap_path);
        return 2;
    }
    std::printf(
        "{\"drawdown_pct\":%.17g,\"var_95\":%.17g,\"var_99\":%.17g,"
        "\"portfolio_equity\":%.17g,\"peak_equity\":%.17g,"
        "\"timestamp_us\":%lld,\"python_pid\":%d,\"armed\":%d,"
        "\"max_drawdown_pct\":%.17g,\"max_var_pct\":%.17g}\n",
        p.drawdown_pct, p.var_95, p.var_99,
        p.portfolio_equity, p.peak_equity,
        (long long)p.timestamp_us, (int)p.python_pid, (int)p.armed,
        p.max_drawdown_pct, p.max_var_pct);
    std::fflush(stdout);
    return 0;
}

int main(int argc, char* argv[]) {
    const char* mmap_path = "/tmp/ayvid_risk.mmap";

    /* Parse argv: supported forms
     *   kill_switch                                   (default path, watchdog)
     *   kill_switch <mmap_path>                       (watchdog)
     *   kill_switch --dump <mmap_path>                (one-shot test reader)
     */
    bool dump_mode = false;
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--dump") == 0) {
            dump_mode = true;
        } else {
            mmap_path = argv[i];
        }
    }

    if (dump_mode) {
        /* No signal handlers, no log file, no curl — just a synchronous
         * seqlock read for the pytest integration. */
        return dump_once(mmap_path);
    }

    struct sigaction sa{};
    sa.sa_handler = signal_handler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = 0;
    sigaction(SIGINT, &sa, nullptr);
    sigaction(SIGTERM, &sa, nullptr);

    g_logfile = std::fopen("/tmp/ayvid_kill.log", "a");
    if (!g_logfile)
        log_msg("WARN", "cannot open /tmp/ayvid_kill.log: %s", std::strerror(errno));

#if AYVID_HAVE_CURL
    curl_global_init(CURL_GLOBAL_DEFAULT);
#endif

    int fd = -1;
    void* mapped = nullptr;
    if (open_risk_frame(mmap_path, &fd, &mapped) != 0) {
#if AYVID_HAVE_CURL
        curl_global_cleanup();
#endif
        if (g_logfile) std::fclose(g_logfile);
        return 1;
    }

    const RiskStateFrame* frame = static_cast<const RiskStateFrame*>(mapped);

    log_msg("INFO",
        "watchdog started — monitoring %s (%zu-byte seqlock frame, payload %zu)",
        mmap_path, RISK_FRAME_SIZE, sizeof(RiskStatePayload));

    constexpr auto tick = std::chrono::milliseconds(100);
    uint64_t cycle = 0;
    constexpr uint64_t heartbeat_cycles = 600; // 60s at 10Hz
    uint64_t torn_reads = 0;
    int64_t  last_good_ts_us = 0;
    uint64_t stale_ticks = 0;

    while (g_running.load(std::memory_order_relaxed)) {
        RiskStatePayload risk{};
        bool ok = read_risk_state_seqlock(frame, risk);

        if (!ok) {
            /* Bounded retries exhausted — writer is either very busy or
             * wedged. Log (rate-limited) and skip this tick: we would
             * rather miss one breach check than act on torn bytes. */
            ++torn_reads;
            if ((torn_reads % 50ULL) == 1ULL) {
                log_msg("WARN",
                    "seqlock read failed — %llu torn reads since start; "
                    "skipping breach check for this tick",
                    (unsigned long long)torn_reads);
            }
            std::this_thread::sleep_for(tick);
            ++cycle;
            continue;
        }

        const double  dd        = risk.drawdown_pct;
        const double  v95       = risk.var_95;
        const double  v99       = risk.var_99;
        const double  eq        = risk.portfolio_equity;
        const int32_t pid       = risk.python_pid;
        const int32_t arm       = risk.armed;
        const double  dd_limit  = risk.max_drawdown_pct;
        const double  var_limit = risk.max_var_pct;

        /* Staleness guard: Python writes at 10Hz. If the timestamp has
         * not advanced for > 5s while armed, the Python writer is
         * frozen — that's itself a breach of the watchdog contract. */
        if (risk.timestamp_us != last_good_ts_us) {
            last_good_ts_us = risk.timestamp_us;
            stale_ticks = 0;
        } else {
            ++stale_ticks;
        }
        const bool stale_breach = (arm == 1) && (stale_ticks >= 50ULL);

        if (arm == 1) {
            bool dd_breach  = std::isfinite(dd)  && dd  > dd_limit  && dd_limit  > 0.0;
            bool var_breach = std::isfinite(v95) && v95 > var_limit && var_limit > 0.0;

            if (dd_breach || var_breach || stale_breach) {
                const char* cause =
                    stale_breach ? "WRITER_FROZEN" :
                    dd_breach    ? "DRAWDOWN"      : "VAR";
                log_msg("CRITICAL",
                    "KILL SWITCH TRIGGERED [%s] — DD=%.4f%% (limit %.2f%%) | "
                    "VaR95=%.4f%% (limit %.2f%%) | equity=$%.2f | PID=%d | "
                    "stale_ticks=%llu",
                    cause, dd, dd_limit, v95, var_limit, eq, pid,
                    (unsigned long long)stale_ticks);

                /* Do both in parallel: broker-side flatten cannot wait
                 * on the Python SIGKILL and vice-versa. */
                std::thread rest_thread(ib_rest_flatten);
                terminate_process(static_cast<pid_t>(pid));
                if (rest_thread.joinable()) rest_thread.join();

                log_msg("CRITICAL", "kill sequence complete — exiting watchdog");
                break;
            }
        }

        if (cycle % heartbeat_cycles == 0) {
            log_msg("INFO",
                "heartbeat — DD=%.4f%% VaR95=%.4f%% VaR99=%.4f%% eq=$%.2f "
                "armed=%s torn=%llu stale_ticks=%llu",
                dd, v95, v99, eq, arm ? "YES" : "NO",
                (unsigned long long)torn_reads,
                (unsigned long long)stale_ticks);
        }

        std::this_thread::sleep_for(tick);
        ++cycle;
    }

    munmap(mapped, RISK_FRAME_SIZE);
    close(fd);
#if AYVID_HAVE_CURL
    curl_global_cleanup();
#endif
    if (g_logfile) std::fclose(g_logfile);
    log_msg("INFO", "watchdog shutdown complete");
    return 0;
}
