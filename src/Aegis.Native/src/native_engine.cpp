#include "aegis_native.h"

#include <Windows.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstddef>
#include <memory>
#include <mutex>
#include <numeric>
#include <thread>
#include <vector>

#if !defined(_M_X64)
#error Aegis.Native requires Windows x64.
#endif

static_assert(sizeof(aegis_frame_t) == AEGIS_FRAME_SIZE);
static_assert(offsetof(aegis_frame_t, actual_flow_lpm) == 24);
static_assert(offsetof(aegis_frame_t, quality) == 40);
static_assert(sizeof(aegis_snapshot_t) == AEGIS_SNAPSHOT_SIZE);
static_assert(offsetof(aegis_snapshot_t, accepted_frame_count) == 32);
static_assert(offsetof(aegis_snapshot_t, sample_count) == 64);

namespace {

constexpr int64_t max_observation_gap_ms = 1500;

struct CloseHandleOnExit {
    void operator()(HANDLE handle) const noexcept { CloseHandle(handle); }
};

bool valid_flow(double flow) noexcept
{
    return std::isfinite(flow) && flow >= 0 && flow <= 100;
}

bool valid_frame(const aegis_frame_t& frame) noexcept
{
    return frame.source_run_token != 0 && frame.sequence >= 0 && frame.observed_unix_ms >= 0
        && frame.quality <= AEGIS_QUALITY_BAD && frame.reserved == 0
        && (frame.quality != AEGIS_QUALITY_VALID
            || (valid_flow(frame.actual_flow_lpm) && valid_flow(frame.setpoint_flow_lpm)));
}

template <typename Operation>
int32_t abi_call(Operation operation) noexcept
{
    try {
        return operation();
    } catch (...) {
        return AEGIS_INTERNAL_ERROR;
    }
}

}

struct aegis_engine {
    explicit aegis_engine(std::size_t capacity) : queue(capacity)
    {
        snapshot.last_sequence = -1;
    }

    std::mutex mutex;
    std::condition_variable work_available;
    std::vector<aegis_frame_t> queue;
    std::size_t read_at = 0;
    std::size_t write_at = 0;
    std::size_t queued = 0;
    std::array<double, AEGIS_WINDOW_SIZE> deviations{};
    std::size_t next_deviation = 0;
    int64_t high_observed_ms = 0;
    aegis_snapshot_t snapshot{};
    std::thread worker;

    void clear_window() noexcept
    {
        next_deviation = 0;
        snapshot.sample_count = 0;
        snapshot.mean_absolute_deviation_lpm = 0;
        snapshot.result_state = AEGIS_RESULT_WARMING_UP;
    }

    void observe(const aegis_frame_t& frame) noexcept
    {
        ++snapshot.processed_frame_count;
        const bool same_run = frame.source_run_token == snapshot.source_run_token;
        if (frame.source_run_token < snapshot.source_run_token
            || (same_run && frame.sequence <= snapshot.last_sequence)) {
            ++snapshot.ignored_frame_count;
            return;
        }

        const bool reversed_time = same_run && frame.observed_unix_ms <= high_observed_ms;
        if (!same_run || frame.sequence - snapshot.last_sequence != 1 || reversed_time
            || frame.observed_unix_ms - high_observed_ms > max_observation_gap_ms) {
            clear_window();
        }
        snapshot.source_run_token = frame.source_run_token;
        snapshot.last_sequence = frame.sequence;
        snapshot.observed_unix_ms = frame.observed_unix_ms;

        if (reversed_time) {
            ++snapshot.ignored_frame_count;
            return;
        }
        high_observed_ms = frame.observed_unix_ms;
        if (frame.quality != AEGIS_QUALITY_VALID) {
            clear_window();
            return;
        }

        deviations[next_deviation] = std::abs(frame.actual_flow_lpm - frame.setpoint_flow_lpm);
        next_deviation = (next_deviation + 1) % deviations.size();
        if (snapshot.sample_count < AEGIS_WINDOW_SIZE) {
            ++snapshot.sample_count;
        }
        if (snapshot.sample_count == AEGIS_WINDOW_SIZE) {
            snapshot.mean_absolute_deviation_lpm =
                std::accumulate(deviations.begin(), deviations.end(), 0.0) / static_cast<double>(deviations.size());
            snapshot.result_state = AEGIS_RESULT_READY;
        }
    }

    void discard_queue() noexcept
    {
        snapshot.discarded_frame_count += queued;
        queued = 0;
    }

    void run() noexcept
    {
        try {
            for (;;) {
                std::unique_lock lock(mutex);
                work_available.wait(lock, [this] {
                    return queued != 0 || snapshot.engine_state != AEGIS_ENGINE_RUNNING;
                });
                if (snapshot.engine_state == AEGIS_ENGINE_CANCELING) {
                    discard_queue();
                }
                if (queued == 0) {
                    snapshot.engine_state = AEGIS_ENGINE_STOPPED;
                    return;
                }
                observe(queue[read_at]);
                read_at = (read_at + 1) % queue.size();
                --queued;
            }
        } catch (...) {
            const std::lock_guard lock(mutex);
            discard_queue();
            snapshot.engine_state = AEGIS_ENGINE_FAILED;
        }
    }
};

uint32_t AEGIS_CALL aegis_abi_version(void)
{
    return AEGIS_ABI_VERSION;
}

int32_t AEGIS_CALL aegis_create(uint32_t capacity, aegis_handle_t* out_handle)
{
    if (out_handle == nullptr) {
        return AEGIS_INVALID_ARGUMENT;
    }
    *out_handle = nullptr;
    if (capacity == 0 || capacity > AEGIS_MAX_CAPACITY) {
        return AEGIS_INVALID_ARGUMENT;
    }
    return abi_call([&] {
        *out_handle = std::make_unique<aegis_engine>(capacity).release();
        return AEGIS_OK;
    });
}

int32_t AEGIS_CALL aegis_start(aegis_handle_t handle)
{
    if (handle == nullptr) {
        return AEGIS_INVALID_ARGUMENT;
    }
    return abi_call([&] {
        const std::lock_guard lock(handle->mutex);
        if (handle->snapshot.engine_state != AEGIS_ENGINE_CREATED) {
            return AEGIS_INVALID_STATE;
        }
        handle->worker = std::thread(&aegis_engine::run, handle);
        handle->snapshot.engine_state = AEGIS_ENGINE_RUNNING;
        return AEGIS_OK;
    });
}

int32_t AEGIS_CALL aegis_submit_frames(aegis_handle_t handle, const aegis_frame_t* frames,
    uint32_t count, uint32_t frame_size, uint32_t* out_accepted)
{
    if (out_accepted == nullptr) {
        return AEGIS_INVALID_ARGUMENT;
    }
    *out_accepted = 0;
    if (handle == nullptr || frames == nullptr || count == 0 || count > AEGIS_MAX_SUBMIT
        || frame_size != sizeof(aegis_frame_t) || !std::all_of(frames, frames + count, valid_frame)) {
        return AEGIS_INVALID_ARGUMENT;
    }

    return abi_call([&] {
        const std::lock_guard lock(handle->mutex);
        if (handle->snapshot.engine_state != AEGIS_ENGINE_RUNNING) {
            return AEGIS_INVALID_STATE;
        }
        const auto accepted = std::min<std::size_t>(count, handle->queue.size() - handle->queued);
        for (std::size_t index = 0; index < accepted; ++index) {
            handle->queue[handle->write_at] = frames[index];
            handle->write_at = (handle->write_at + 1) % handle->queue.size();
        }
        handle->queued += accepted;
        handle->snapshot.accepted_frame_count += accepted;
        *out_accepted = static_cast<uint32_t>(accepted);
        if (accepted > 0) {
            handle->work_available.notify_one();
        }
        return accepted == count ? AEGIS_OK : AEGIS_QUEUE_FULL;
    });
}

int32_t AEGIS_CALL aegis_read_snapshot(aegis_handle_t handle,
    aegis_snapshot_t* out_snapshot, uint32_t snapshot_size)
{
    if (handle == nullptr || out_snapshot == nullptr || snapshot_size != sizeof(aegis_snapshot_t)) {
        return AEGIS_INVALID_ARGUMENT;
    }
    return abi_call([&] {
        const std::lock_guard lock(handle->mutex);
        *out_snapshot = handle->snapshot;
        return AEGIS_OK;
    });
}

int32_t AEGIS_CALL aegis_request_stop(aegis_handle_t handle, uint32_t mode)
{
    if (handle == nullptr || mode > AEGIS_STOP_CANCEL) {
        return AEGIS_INVALID_ARGUMENT;
    }
    return abi_call([&] {
        const std::lock_guard lock(handle->mutex);
        auto& state = handle->snapshot.engine_state;
        if (state == AEGIS_ENGINE_CREATED) {
            return AEGIS_INVALID_STATE;
        }
        if (state == AEGIS_ENGINE_RUNNING
            || (state == AEGIS_ENGINE_DRAINING && mode == AEGIS_STOP_CANCEL)) {
            state = mode == AEGIS_STOP_DRAIN ? AEGIS_ENGINE_DRAINING : AEGIS_ENGINE_CANCELING;
            handle->work_available.notify_one();
        }
        return AEGIS_OK;
    });
}

int32_t AEGIS_CALL aegis_wait_stopped(aegis_handle_t handle, uint32_t timeout_ms)
{
    if (handle == nullptr || timeout_ms == INFINITE) {
        return AEGIS_INVALID_ARGUMENT;
    }
    return abi_call([&] {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
        std::unique_lock lock(handle->mutex);
        if (handle->snapshot.engine_state == AEGIS_ENGINE_CREATED) {
            return AEGIS_INVALID_STATE;
        }
        if (handle->worker.joinable()) {
            // The duplicate stays open even if another waiter joins first.
            // Wait for OS thread exit without holding the snapshot/queue lock.
            const HANDLE process = GetCurrentProcess();
            HANDLE duplicate = nullptr;
            if (!DuplicateHandle(process, handle->worker.native_handle(), process,
                    &duplicate, SYNCHRONIZE, FALSE, 0)) {
                return AEGIS_INTERNAL_ERROR;
            }
            const std::unique_ptr<void, CloseHandleOnExit> wait_handle(duplicate);
            lock.unlock();
            const auto remaining = std::chrono::ceil<std::chrono::milliseconds>(
                deadline - std::chrono::steady_clock::now());
            const auto wait_ms = remaining.count() > 0 ? static_cast<DWORD>(remaining.count()) : 0;
            const DWORD result = WaitForSingleObject(wait_handle.get(), wait_ms);
            if (result != WAIT_OBJECT_0) {
                return result == WAIT_TIMEOUT ? AEGIS_TIMEOUT : AEGIS_INTERNAL_ERROR;
            }
            lock.lock();
            if (handle->worker.joinable()) {
                handle->worker.join();
            }
        }
        return handle->snapshot.engine_state == AEGIS_ENGINE_FAILED ? AEGIS_INTERNAL_ERROR : AEGIS_OK;
    });
}

int32_t AEGIS_CALL aegis_release(aegis_handle_t handle)
{
    if (handle == nullptr) {
        return AEGIS_INVALID_ARGUMENT;
    }
    // The ABI requires exclusive ownership in this call.
    if (handle->worker.joinable()) {
        return AEGIS_BUSY;
    }
    delete handle;
    return AEGIS_OK;
}
