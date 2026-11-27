#include <aegis_native.h>

#include <Windows.h>

#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <iostream>
#include <latch>
#include <limits>
#include <source_location>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>

namespace {

constexpr auto wait_limit_ms = uint32_t{5000};

void require(bool condition, std::string_view message,
             std::source_location location = std::source_location::current()) {
    if (!condition) {
        throw std::runtime_error(std::to_string(location.line()) + ": " + std::string{message});
    }
}

void status_is(int32_t actual, int32_t expected = AEGIS_OK,
               std::source_location location = std::source_location::current()) {
    if (actual != expected) {
        require(false, "expected status " + std::to_string(expected) +
                           ", received " + std::to_string(actual), location);
    }
}

class Engine {
public:
    explicit Engine(uint32_t capacity = 256) {
        status_is(aegis_create(capacity, &handle_));
        require(handle_ != nullptr, "create returned a valid handle");
    }

    ~Engine() {
        if (handle_ != nullptr && aegis_release(handle_) == AEGIS_BUSY) {
            aegis_request_stop(handle_, AEGIS_STOP_CANCEL);
            const auto status = aegis_wait_stopped(handle_, wait_limit_ms);
            if (status == AEGIS_OK || status == AEGIS_INTERNAL_ERROR) {
                aegis_release(handle_);
            }
        }
    }

    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;

    [[nodiscard]] aegis_handle_t get() const { return handle_; }

    void start() const { status_is(aegis_start(handle_)); }

    [[nodiscard]] aegis_snapshot_t snapshot() const {
        aegis_snapshot_t value{};
        status_is(aegis_read_snapshot(handle_, &value, sizeof(value)));
        return value;
    }

    void submit(const aegis_frame_t* frames, uint32_t count = 1) const {
        uint32_t accepted = 0;
        status_is(aegis_submit_frames(handle_, frames, count, sizeof(*frames), &accepted));
        require(accepted == count, "queue accepted all frames");
    }

    [[nodiscard]] aegis_snapshot_t processed(uint64_t count) const {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds{wait_limit_ms};
        do {
            const auto state = snapshot();
            if (state.processed_frame_count >= count) {
                return state;
            }
            std::this_thread::yield();
        } while (std::chrono::steady_clock::now() < deadline);
        throw std::runtime_error("worker did not process the submitted frames");
    }

    void drain() const {
        status_is(aegis_request_stop(handle_, AEGIS_STOP_DRAIN));
        status_is(aegis_wait_stopped(handle_, wait_limit_ms));
    }

    void release() {
        status_is(aegis_release(handle_));
        handle_ = nullptr;
    }

private:
    aegis_handle_t handle_{};
};

aegis_frame_t frame(int64_t sequence, uint64_t run = 1) {
    aegis_frame_t value{};
    value.source_run_token = run;
    value.sequence = sequence;
    value.observed_unix_ms = 10000 + 100 * sequence;
    value.actual_flow_lpm = 8.0;
    value.setpoint_flow_lpm = 10.0;
    value.quality = AEGIS_QUALITY_VALID;
    return value;
}

template <std::size_t size>
std::array<aegis_frame_t, size> consecutive_frames(int64_t first_sequence = 0, uint64_t run = 1) {
    std::array<aegis_frame_t, size> result{};
    for (std::size_t i = 0; i < result.size(); ++i) {
        result[i] = frame(first_sequence + static_cast<int64_t>(i), run);
    }
    return result;
}

void validation() {
    require(aegis_abi_version() == AEGIS_ABI_VERSION, "ABI version");
    aegis_handle_t invalid{};
    status_is(aegis_create(0, &invalid), AEGIS_INVALID_ARGUMENT);
    status_is(aegis_create(AEGIS_MAX_CAPACITY + 1, &invalid), AEGIS_INVALID_ARGUMENT);
    status_is(aegis_create(1, nullptr), AEGIS_INVALID_ARGUMENT);
    status_is(aegis_start(nullptr), AEGIS_INVALID_ARGUMENT);
    require(invalid == nullptr, "invalid create leaves no handle");

    Engine engine;
    const auto created = engine.snapshot();
    require(created.engine_state == AEGIS_ENGINE_CREATED, "new engine state");
    require(created.result_state == AEGIS_RESULT_EMPTY && created.last_sequence == -1 &&
                created.sample_count == 0, "empty snapshot");
    status_is(aegis_wait_stopped(engine.get(), 0), AEGIS_INVALID_STATE);
    status_is(aegis_request_stop(engine.get(), AEGIS_STOP_DRAIN), AEGIS_INVALID_STATE);
    engine.start();
    status_is(aegis_start(engine.get()), AEGIS_INVALID_STATE);
    status_is(aegis_request_stop(engine.get(), 0xffffffffu), AEGIS_INVALID_ARGUMENT);
    status_is(aegis_wait_stopped(engine.get(), UINT32_MAX), AEGIS_INVALID_ARGUMENT);

    aegis_snapshot_t read{};
    read.last_sequence = 81;
    status_is(aegis_read_snapshot(engine.get(), &read, sizeof(read) - 1), AEGIS_INVALID_ARGUMENT);
    require(read.last_sequence == 81, "wrong output size leaves the caller's buffer unchanged");
    status_is(aegis_read_snapshot(engine.get(), nullptr, sizeof(read)), AEGIS_INVALID_ARGUMENT);

    const auto good = frame(0);
    uint32_t accepted = 0;
    status_is(aegis_submit_frames(engine.get(), &good, 1, sizeof(good) - 1, &accepted), AEGIS_INVALID_ARGUMENT);
    status_is(aegis_submit_frames(engine.get(), &good, 1, sizeof(good), nullptr), AEGIS_INVALID_ARGUMENT);
    status_is(aegis_submit_frames(engine.get(), nullptr, 1, sizeof(good), &accepted), AEGIS_INVALID_ARGUMENT);
    status_is(aegis_submit_frames(engine.get(), &good, 0, sizeof(good), &accepted), AEGIS_INVALID_ARGUMENT);
    const auto oversized = consecutive_frames<AEGIS_MAX_SUBMIT + 1>();
    status_is(aegis_submit_frames(engine.get(), oversized.data(), static_cast<uint32_t>(oversized.size()),
                                 sizeof(good), &accepted), AEGIS_INVALID_ARGUMENT);

    const auto invalid_frame_is_rejected = [&](const aegis_frame_t& bad) {
        const std::array batch{good, bad};
        accepted = 123;
        status_is(aegis_submit_frames(engine.get(), batch.data(), static_cast<uint32_t>(batch.size()),
                                      sizeof(good), &accepted), AEGIS_INVALID_ARGUMENT);
        require(accepted == 0, "validation precedes admission of the entire batch");
    };
    auto bad = frame(1);
    bad.source_run_token = 0;
    invalid_frame_is_rejected(bad);
    bad = frame(1);
    bad.sequence = -1;
    invalid_frame_is_rejected(bad);
    bad = frame(1);
    bad.observed_unix_ms = -1;
    invalid_frame_is_rejected(bad);
    bad = frame(1);
    bad.quality = 0xffffffffu;
    invalid_frame_is_rejected(bad);
    bad = frame(1);
    bad.reserved = 1;
    invalid_frame_is_rejected(bad);
    bad = frame(1);
    bad.actual_flow_lpm = std::numeric_limits<double>::quiet_NaN();
    invalid_frame_is_rejected(bad);
    bad = frame(1);
    bad.setpoint_flow_lpm = std::numeric_limits<double>::infinity();
    invalid_frame_is_rejected(bad);
    bad = frame(1);
    bad.actual_flow_lpm = -1;
    invalid_frame_is_rejected(bad);
    bad = frame(1);
    bad.setpoint_flow_lpm = 100.01;
    invalid_frame_is_rejected(bad);

    engine.drain();
    const auto stopped = engine.snapshot();
    require(stopped.accepted_frame_count == 0 && stopped.processed_frame_count == 0,
            "invalid calls did not change the stream");
    engine.release();
}

void rolling_window() {
    static_assert(AEGIS_WINDOW_SIZE == 32);
    Engine engine;
    engine.start();
    auto input = consecutive_frames<33>();
    input[31].actual_flow_lpm = 0.0;
    input[32].actual_flow_lpm = 10.0;
    engine.submit(input.data(), 31);
    const auto warming = engine.processed(31);
    require(warming.sample_count == 31 && warming.result_state == AEGIS_RESULT_WARMING_UP,
            "a 31-sample window still warms up");
    engine.submit(&input[31]);
    const auto ready = engine.processed(32);
    require(ready.sample_count == 32 && ready.result_state == AEGIS_RESULT_READY,
            "a true zero is eligible for the complete window");
    require(std::abs(ready.mean_absolute_deviation_lpm - (62.0 + 10.0) / 32.0) < 1e-12,
            "mean includes the absolute deviation of the zero sample");
    require(ready.source_run_token == 1 && ready.last_sequence == 31 &&
                ready.observed_unix_ms == input[31].observed_unix_ms,
            "snapshot carries provenance of the newest sample");

    engine.submit(&input[32]);
    engine.drain();
    const auto final = engine.snapshot();
    require(final.sample_count == 32 && final.result_state == AEGIS_RESULT_READY,
            "rolling window retains exactly 32 samples");
    require(std::abs(final.mean_absolute_deviation_lpm - 70.0 / 32.0) < 1e-12,
            "the oldest absolute deviation is removed");
    require(final.last_sequence == 32 && final.processed_frame_count == 33 &&
                final.discarded_frame_count == 0, "drain preserves the final frame");
    engine.release();
}

void discontinuities() {
    Engine engine;
    engine.start();
    const auto baseline = consecutive_frames<32>(0, 2);
    engine.submit(baseline.data(), static_cast<uint32_t>(baseline.size()));

    auto replay = frame(31, 2);
    replay.actual_flow_lpm = 100.0;
    const auto retired_run = frame(32, 1);
    const std::array ignored{replay, retired_run};
    engine.submit(ignored.data(), static_cast<uint32_t>(ignored.size()));
    const auto current = engine.processed(34);
    require(current.result_state == AEGIS_RESULT_READY && current.ignored_frame_count == 2 &&
                current.source_run_token == 2 && current.last_sequence == 31 &&
                current.mean_absolute_deviation_lpm == 2.0,
            "duplicates and retired runs do not affect the result");

    auto missing = frame(32, 2);
    missing.quality = AEGIS_QUALITY_MISSING;
    missing.actual_flow_lpm = std::numeric_limits<double>::quiet_NaN();
    engine.submit(&missing);
    auto state = engine.processed(35);
    require(state.result_state != AEGIS_RESULT_READY && state.sample_count == 0,
            "missing quality clears the previous window");

    const auto following = consecutive_frames<3>(33, 2);
    engine.submit(following.data(), static_cast<uint32_t>(following.size()));
    state = engine.processed(38);
    require(state.sample_count == 3, "good frames after a missing observation start a fresh window");

    auto bad = frame(36, 2);
    bad.quality = AEGIS_QUALITY_BAD;
    engine.submit(&bad);
    state = engine.processed(39);
    require(state.result_state != AEGIS_RESULT_READY && state.sample_count == 0,
            "bad quality clears the previous window");

    const std::array gap{frame(37, 2), frame(39, 2)};
    engine.submit(gap.data(), static_cast<uint32_t>(gap.size()));
    state = engine.processed(41);
    require(state.sample_count == 1 && state.last_sequence == 39, "sequence gap starts a fresh window");

    auto timestamp_gap = frame(40, 2);
    timestamp_gap.observed_unix_ms = gap.back().observed_unix_ms + 1501;
    engine.submit(&timestamp_gap);
    state = engine.processed(42);
    require(state.sample_count == 1, "timing gap starts a fresh window");

    auto backwards = frame(41, 2);
    backwards.observed_unix_ms = timestamp_gap.observed_unix_ms - 1;
    engine.submit(&backwards);
    state = engine.processed(43);
    require(state.result_state != AEGIS_RESULT_READY && state.sample_count == 0,
            "reversed timestamp clears the window and is excluded");

    const auto next_run = frame(0, 3);
    engine.submit(&next_run);
    state = engine.processed(44);
    require(state.sample_count == 1 && state.source_run_token == 3 && state.last_sequence == 0,
            "new run may restart both sequence and observation time");

    auto next_to_max = frame(0, 4);
    next_to_max.sequence = std::numeric_limits<int64_t>::max() - 1;
    auto max_frame = frame(1, 4);
    max_frame.sequence = std::numeric_limits<int64_t>::max();
    const std::array max_range{next_to_max, max_frame, max_frame};
    engine.submit(max_range.data(), static_cast<uint32_t>(max_range.size()));
    engine.drain();
    state = engine.snapshot();
    require(state.sample_count == 2 && state.last_sequence == max_frame.sequence &&
                state.ignored_frame_count == 4,
            "maximum signed sequence remains eligible and a replay stays ignored");
    engine.release();
}

void queue_drain() {
    Engine engine{1};
    engine.start();
    auto input = consecutive_frames<2>();
    uint32_t accepted = 0;
    status_is(aegis_submit_frames(engine.get(), input.data(), static_cast<uint32_t>(input.size()),
                                  sizeof(input.front()), &accepted), AEGIS_QUEUE_FULL);
    require(accepted == 1, "capacity one accepts only the prefix");
    const auto first = engine.processed(1);
    require(first.last_sequence == 0 && first.accepted_frame_count == 1, "queue processed only the accepted prefix");

    engine.submit(&input[1]);
    status_is(aegis_request_stop(engine.get(), AEGIS_STOP_DRAIN));
    status_is(aegis_request_stop(engine.get(), AEGIS_STOP_DRAIN));
    accepted = 0;
    status_is(aegis_submit_frames(engine.get(), input.data(), 1, sizeof(input.front()), &accepted),
              AEGIS_INVALID_STATE);
    require(accepted == 0, "no new frame accepted once drain starts");
    status_is(aegis_release(engine.get()), AEGIS_BUSY);
    status_is(aegis_wait_stopped(engine.get(), wait_limit_ms));
    const auto stopped = engine.snapshot();
    require(stopped.engine_state == AEGIS_ENGINE_STOPPED &&
                stopped.accepted_frame_count == 2 && stopped.processed_frame_count == 2 &&
                stopped.discarded_frame_count == 0 && stopped.last_sequence == 1,
            "drain accounts for every accepted frame");
    engine.release();
}

void cancel_and_wait() {
    Engine engine{AEGIS_MAX_CAPACITY};
    engine.start();
    status_is(aegis_wait_stopped(engine.get(), 0), AEGIS_TIMEOUT);
    status_is(aegis_wait_stopped(engine.get(), 1), AEGIS_TIMEOUT);
    require(engine.snapshot().engine_state == AEGIS_ENGINE_RUNNING, "wait timeout leaves the worker running");
    status_is(aegis_release(engine.get()), AEGIS_BUSY);

    const auto input = consecutive_frames<AEGIS_MAX_SUBMIT>();
    engine.submit(input.data(), static_cast<uint32_t>(input.size()));
    status_is(aegis_request_stop(engine.get(), AEGIS_STOP_DRAIN));
    status_is(aegis_request_stop(engine.get(), AEGIS_STOP_CANCEL));
    status_is(aegis_request_stop(engine.get(), AEGIS_STOP_DRAIN));
    status_is(aegis_request_stop(engine.get(), AEGIS_STOP_CANCEL));
    status_is(aegis_wait_stopped(engine.get(), wait_limit_ms));
    status_is(aegis_wait_stopped(engine.get(), 0));
    const auto stopped = engine.snapshot();
    require(stopped.engine_state == AEGIS_ENGINE_STOPPED, "cooperative cancel stops the worker");
    require(stopped.accepted_frame_count == input.size() &&
                stopped.processed_frame_count + stopped.discarded_frame_count == stopped.accepted_frame_count,
            "every admitted frame was processed or discarded");
    std::cout << "Canceled queue: " << stopped.processed_frame_count << " processed, "
              << stopped.discarded_frame_count << " discarded.\n";
    engine.release();
}

void concurrent_lifecycle() {
    const auto input = consecutive_frames<4>();
    const auto lifecycle = [&] {
        Engine engine{4};
        engine.start();
        engine.submit(input.data(), static_cast<uint32_t>(input.size()));
        engine.drain();
        require(engine.snapshot().processed_frame_count == input.size(), "repeated drain processes all frames");
        engine.release();
    };
    lifecycle();
    DWORD initial_handles = 0;
    require(GetProcessHandleCount(GetCurrentProcess(), &initial_handles), "read initial process handle count");
    for (int iteration = 0; iteration < 100; ++iteration) {
        lifecycle();
    }

    Engine engine;
    engine.start();
    engine.submit(input.data(), static_cast<uint32_t>(input.size()));
    std::array<int32_t, 3> waits{};
    std::latch waiters_started{static_cast<std::ptrdiff_t>(waits.size())};
    std::array<std::jthread, 3> waiters;
    for (std::size_t index = 0; index < waiters.size(); ++index) {
        waiters[index] = std::jthread([&, index] {
            waiters_started.count_down();
            waits[index] = aegis_wait_stopped(engine.get(), wait_limit_ms);
        });
    }
    std::atomic<bool> valid_snapshots{false};
    std::latch first_read{1};
    std::jthread reader([&](std::stop_token token) {
        const auto read_consistent = [&] {
            aegis_snapshot_t view{};
            const auto status = aegis_read_snapshot(engine.get(), &view, sizeof(view));
            return status == AEGIS_OK && view.sample_count <= AEGIS_WINDOW_SIZE &&
                   view.processed_frame_count + view.discarded_frame_count <= view.accepted_frame_count;
        };
        valid_snapshots = read_consistent();
        first_read.count_down();
        while (!token.stop_requested() && valid_snapshots.load()) {
            if (!read_consistent()) {
                valid_snapshots = false;
                return;
            }
            std::this_thread::yield();
        }
    });
    waiters_started.wait();
    first_read.wait();
    status_is(aegis_request_stop(engine.get(), AEGIS_STOP_DRAIN));
    for (auto& waiter : waiters) {
        waiter.join();
    }
    reader.request_stop();
    reader.join();

    for (const auto status : waits) {
        status_is(status, AEGIS_OK);
    }
    require(valid_snapshots.load(), "concurrent snapshots remain consistent while stopping");
    require(engine.snapshot().processed_frame_count == input.size(), "concurrent waiters preserve the drain");
    engine.release();
    DWORD final_handles = 0;
    require(GetProcessHandleCount(GetCurrentProcess(), &final_handles), "read final process handle count");
    std::cout << "Process handles: " << initial_handles << " before, " << final_handles << " after.\n";
    require(final_handles <= initial_handles, "native thread and waiter handles were released");
}

}  // namespace

int main(int argc, char** argv) {
    struct TestCase {
        std::string_view name;
        void (*run)();
    };
    constexpr std::array tests{
        TestCase{"validation", validation},
        TestCase{"rolling_window", rolling_window},
        TestCase{"discontinuities", discontinuities},
        TestCase{"queue_drain", queue_drain},
        TestCase{"cancel_and_wait", cancel_and_wait},
        TestCase{"concurrent_lifecycle", concurrent_lifecycle},
    };
    if (argc == 2) {
        for (const auto& test : tests) {
            if (test.name == argv[1]) {
                try {
                    test.run();
                    return 0;
                } catch (const std::exception& error) {
                    std::cerr << test.name << ": " << error.what() << '\n';
                    return 1;
                }
            }
        }
    }
    std::cerr << "Specify the name of one native engine test case.\n";
    return 2;
}
