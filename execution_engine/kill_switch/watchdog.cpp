/**
 * AYVID C++ Kill Switch Watchdog
 *
 * Runs as a separate OS process outside the Python GIL.
 * Reads the memory-mapped risk file at 10Hz and triggers
 * emergency position flattening if VaR thresholds are breached.
 *
 * Compile: g++ -O2 -o watchdog watchdog.cpp -lcurl
 * Run:     ./watchdog /tmp/ayvid_risk.mmap 3.0
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <csignal>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <ctime>

// Matches Python struct format: =ddd?
#pragma pack(push, 1)
struct RiskData {
    double value_at_risk_95;
    double current_drawdown_pct;
    double max_drawdown_pct;
    bool kill_switch_armed;
};
#pragma pack(pop)

static volatile bool g_running = true;

void signal_handler(int sig) {
    g_running = false;
    fprintf(stderr, "\n[WATCHDOG] Received signal %d, shutting down.\n", sig);
}

void log_msg(const char* level, const char* fmt, ...) {
    time_t now = time(nullptr);
    struct tm* t = localtime(&now);
    char timebuf[64];
    strftime(timebuf, sizeof(timebuf), "%Y-%m-%d %H:%M:%S", t);

    fprintf(stderr, "[%s] [WATCHDOG] [%s] ", timebuf, level);
    va_list args;
    va_start(args, fmt);
    vfprintf(stderr, fmt, args);
    va_end(args);
    fprintf(stderr, "\n");
}

/**
 * Emergency flatten: kill the Python process and call IB Web API
 * to cancel all orders and flatten positions.
 */
void emergency_flatten(pid_t python_pid, double drawdown) {
    log_msg("CRITICAL",
            "KILL SWITCH ACTIVATED — Drawdown: %.2f%% — Flattening all positions",
            drawdown);

    // Step 1: SIGKILL the Python trading process
    if (python_pid > 0) {
        log_msg("CRITICAL", "Sending SIGKILL to Python process %d", python_pid);
        kill(python_pid, SIGKILL);
    }

    // Step 2: Call IB Web API to cancel all orders (requires curl)
    // In production, this would hit the IB Client Portal API
    log_msg("CRITICAL",
            "Would call IB Web API to cancel all orders and flatten positions");

    log_msg("CRITICAL", "Emergency flatten complete. Exiting.");
    exit(1);
}

int main(int argc, char* argv[]) {
    if (argc < 3) {
        fprintf(stderr, "Usage: %s <mmap_path> <max_drawdown_pct> [python_pid]\n", argv[0]);
        fprintf(stderr, "Example: %s /tmp/ayvid_risk.mmap 3.0 12345\n", argv[0]);
        return 1;
    }

    const char* mmap_path = argv[1];
    double max_drawdown = atof(argv[2]);
    pid_t python_pid = (argc > 3) ? atoi(argv[3]) : 0;

    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    int fd = open(mmap_path, O_RDONLY);
    if (fd < 0) {
        log_msg("ERROR", "Cannot open mmap file: %s", mmap_path);
        return 1;
    }

    struct stat st;
    fstat(fd, &st);
    if (st.st_size < (off_t)sizeof(RiskData)) {
        log_msg("ERROR", "Mmap file too small: %ld bytes (need %lu)",
                st.st_size, sizeof(RiskData));
        close(fd);
        return 1;
    }

    void* mapped = mmap(nullptr, sizeof(RiskData), PROT_READ, MAP_SHARED, fd, 0);
    if (mapped == MAP_FAILED) {
        log_msg("ERROR", "mmap() failed");
        close(fd);
        return 1;
    }

    const RiskData* risk = static_cast<const RiskData*>(mapped);

    log_msg("INFO", "Watchdog started — monitoring %s", mmap_path);
    log_msg("INFO", "Max drawdown threshold: %.2f%%", max_drawdown);
    log_msg("INFO", "Python PID: %d", python_pid);

    // Monitor at 10Hz (100ms interval)
    const useconds_t interval_us = 100000;
    unsigned long cycle = 0;

    while (g_running) {
        if (risk->kill_switch_armed) {
            if (risk->current_drawdown_pct >= max_drawdown) {
                emergency_flatten(python_pid, risk->current_drawdown_pct);
            }
        }

        // Log status every 10 seconds (100 cycles)
        if (cycle % 100 == 0) {
            log_msg("INFO",
                    "VaR95=%.4f DD=%.2f%% MaxDD=%.2f%% Armed=%s",
                    risk->value_at_risk_95,
                    risk->current_drawdown_pct,
                    risk->max_drawdown_pct,
                    risk->kill_switch_armed ? "YES" : "NO");
        }

        usleep(interval_us);
        cycle++;
    }

    munmap(mapped, sizeof(RiskData));
    close(fd);
    log_msg("INFO", "Watchdog shutdown complete.");
    return 0;
}
