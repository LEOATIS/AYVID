/**
 * AYVID Kill Switch — Memory-mapped VaR watchdog.
 *
 * Runs outside the Python GIL at 10Hz. Reads /tmp/ayvid_risk.mmap
 * and sends SIGTERM/SIGKILL to the trading process on risk breach.
 *
 * Compile: g++ -O2 -std=c++17 -o kill_switch kill_switch.cpp -lpthread
 */

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>

#pragma pack(push, 1)
struct RiskState {
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

static constexpr size_t RISK_STATE_SIZE = sizeof(RiskState);

static std::atomic<bool> g_running{true};

static void signal_handler(int sig) {
    g_running.store(false, std::memory_order_relaxed);
    fprintf(stderr, "\n[WATCHDOG] caught signal %d, shutting down\n", sig);
}

static FILE* g_logfile = nullptr;

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

    char msg[1024];
    std::va_list args;
    va_start(args, fmt);
    std::vsnprintf(msg, sizeof(msg), fmt, args);
    va_end(args);

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

int main(int argc, char* argv[]) {
    const char* mmap_path = "/tmp/ayvid_risk.mmap";
    if (argc > 1) mmap_path = argv[1];

    struct sigaction sa{};
    sa.sa_handler = signal_handler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = 0;
    sigaction(SIGINT, &sa, nullptr);
    sigaction(SIGTERM, &sa, nullptr);

    g_logfile = std::fopen("/tmp/ayvid_kill.log", "a");
    if (!g_logfile)
        log_msg("WARN", "cannot open /tmp/ayvid_kill.log: %s", std::strerror(errno));

    int fd = open(mmap_path, O_RDONLY);
    if (fd < 0) {
        log_msg("ERROR", "cannot open %s: %s", mmap_path, std::strerror(errno));
        return 1;
    }

    struct stat st{};
    if (fstat(fd, &st) < 0 || static_cast<size_t>(st.st_size) < RISK_STATE_SIZE) {
        log_msg("ERROR", "mmap file too small (%ld bytes, need %zu)", st.st_size, RISK_STATE_SIZE);
        close(fd);
        return 1;
    }

    void* mapped = mmap(nullptr, RISK_STATE_SIZE, PROT_READ, MAP_SHARED, fd, 0);
    if (mapped == MAP_FAILED) {
        log_msg("ERROR", "mmap failed: %s", std::strerror(errno));
        close(fd);
        return 1;
    }

    const volatile RiskState* risk = static_cast<const volatile RiskState*>(mapped);

    log_msg("INFO", "watchdog started — monitoring %s (%zu bytes)", mmap_path, RISK_STATE_SIZE);

    constexpr auto tick = std::chrono::milliseconds(100);
    uint64_t cycle = 0;
    constexpr uint64_t heartbeat_cycles = 600; // 60s at 10Hz

    while (g_running.load(std::memory_order_relaxed)) {
        double dd   = risk->drawdown_pct;
        double v95  = risk->var_95;
        double v99  = risk->var_99;
        double eq   = risk->portfolio_equity;
        int32_t pid = risk->python_pid;
        int32_t arm = risk->armed;
        double dd_limit  = risk->max_drawdown_pct;
        double var_limit = risk->max_var_pct;

        if (arm == 1) {
            bool dd_breach  = std::isfinite(dd)  && dd  > dd_limit  && dd_limit  > 0.0;
            bool var_breach = std::isfinite(v95) && v95 > var_limit && var_limit > 0.0;

            if (dd_breach || var_breach) {
                log_msg("CRITICAL",
                    "KILL SWITCH TRIGGERED — DD=%.4f%% (limit %.2f%%) | "
                    "VaR95=%.4f%% (limit %.2f%%) | equity=$%.2f | PID=%d",
                    dd, dd_limit, v95, var_limit, eq, pid);
                terminate_process(static_cast<pid_t>(pid));
                log_msg("CRITICAL", "kill sequence complete — exiting watchdog");
                break;
            }
        }

        if (cycle % heartbeat_cycles == 0) {
            log_msg("INFO",
                "heartbeat — DD=%.4f%% VaR95=%.4f%% VaR99=%.4f%% eq=$%.2f armed=%s",
                dd, v95, v99, eq, arm ? "YES" : "NO");
        }

        std::this_thread::sleep_for(tick);
        ++cycle;
    }

    munmap(mapped, RISK_STATE_SIZE);
    close(fd);
    if (g_logfile) std::fclose(g_logfile);
    log_msg("INFO", "watchdog shutdown complete");
    return 0;
}
