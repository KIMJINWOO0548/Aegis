"""Profile bounded synthetic replay; compare serializers without weakening durability."""

import argparse
import asyncio
import cProfile
import io
import json
import pstats
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'emulators/Aegis.Emulator'))
from aegis_emulator.core import SnapshotGenerator
from aegis.archive import RawArchive
from aegis.collector import Collector, RawSnapshot, ReplayPage, SourceConfig


async def profile(legacy):
    generator = SnapshotGenerator('PROFILE', signal_count=256, interval_ms=10)
    generator.started_monotonic -= 2
    generator.started_utc = datetime.now(timezone.utc)
    generator.started_utc = generator.started_utc.replace(year=2025)
    stages = {}
    started = time.perf_counter()
    pages = []
    after = -1
    while after < 199:
        page = generator.batch(after, min(256, 200 - after - 1))
        pages.append(page)
        after = page['snapshots'][-1]['sequence']
    stages['source_generate_seconds'] = time.perf_counter() - started
    encoded = [json.dumps(page, separators=(',', ':')).encode() for page in pages]
    started = time.perf_counter()
    decoded = [ReplayPage.model_validate_json(page) for page in encoded]
    stages['decode_validate_seconds'] = time.perf_counter() - started
    with tempfile.TemporaryDirectory(prefix='aegis-profile-') as directory:
        archive = RawArchive(Path(directory) / 'raw.sqlite3')
        await archive.start()
        collector = Collector([SourceConfig(deviceId='PROFILE', baseUrl='http://127.0.0.1:19101')],
                              {'AEGIS_EMULATOR_TOKEN': 'profile-' + 'x'*32})
        try:
            started = time.perf_counter()
            for page in decoded:
                if legacy:
                    await archive.append([s.model_dump(mode='json') for s in page.snapshots], datetime.now(timezone.utc))
                else:
                    await archive.append_validated(page.snapshots, datetime.now(timezone.utc))
            stages['archive_serialize_and_commit_seconds'] = time.perf_counter() - started
            assert await archive.cursor('PROFILE') == (generator.run_id, 199)
            assert await archive.latest('PROFILE') == decoded[-1].snapshots[-1].model_dump(mode='json')
            started = time.perf_counter()
            for page in decoded:
                for snapshot in page.snapshots:
                    collector._accept(0, snapshot, history=True)
            stages['projection_seconds'] = time.perf_counter() - started
        finally:
            await archive.close()
    print(json.dumps({'snapshots':200, 'signals':256, 'pages':len(pages),
                     'serialization':'legacy-dictionary' if legacy else 'validated-model-json', 'stages':stages}, indent=2))


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--legacy-serialization', action='store_true', help='Measure the earlier dict/JSON path on identical validated data.')
args = parser.parse_args()
with cProfile.Profile() as profiler:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(profile(args.legacy_serialization))
stream = io.StringIO()
pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats('cumtime').print_stats(30)
print(stream.getvalue())

# Measure CPU separately after stopping cProfile; instrumentation changes Python/Rust comparisons.
generator = SnapshotGenerator('SERIALIZATION', signal_count=256, interval_ms=10)
models = [RawSnapshot.model_validate(generator._snapshot_at(index, replay=True)) for index in range(200)]
serializers = {
    'legacy-dictionary': lambda item: json.dumps(item.model_dump(mode='json'), sort_keys=True,
        separators=(',', ':'), ensure_ascii=False, allow_nan=False),
    'validated-model-json': lambda item: item.model_dump_json(),
}
assert all(json.loads(serializers['legacy-dictionary'](item)) ==
           json.loads(serializers['validated-model-json'](item)) for item in models)
samples = {name: [] for name in serializers}
for trial in range(6):
    for name in list(serializers)[::1 if trial % 2 == 0 else -1]:
        started = time.process_time_ns()
        payloads = [serializers[name](item) for item in models]
        samples[name].append((time.process_time_ns() - started) / 1_000_000)
        assert all(payloads)
print(json.dumps({'scope': 'Serialization CPU only; six alternating trials, no cProfile or disk/network.',
    'framesPerTrial': 200, 'signalsPerFrame': 256, 'semanticEquality': True,
    'cpuMilliseconds': samples,
    'medianCpuMilliseconds': {name: statistics.median(values) for name, values in samples.items()}}, indent=2))
