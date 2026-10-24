"""Startup transitions without registry edits, process launches or account reads."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pet_autostart import follow_codex, is_codex_image

assert is_codex_image(r'C:\Program Files\WindowsApps\OpenAI.Codex_26.1021.2667.0_x64__id\app\ChatGPT.exe')
assert is_codex_image(r'D:\WindowsApps\OpenAI.Codex_27.1_x64__id\app\Codex.exe')
assert is_codex_image(r'C:\Users\Demo\AppData\Local\OpenAI\Codex\app\ChatGPT.exe')
assert not is_codex_image(r'C:\Program Files\WindowsApps\OpenAI.ChatGPT_1_x64__id\app\ChatGPT.exe')
assert not is_codex_image(r'C:\Users\Demo\AppData\Local\OpenAI\Codex\bin\1\codex.exe')
assert not is_codex_image(r'C:\Program Files\WindowsApps\OpenAI.Codex_1_x64__id\app\resources\codex.exe')
assert not is_codex_image('')


def exercise(scans, fail_launch=False):
    index, starts, errors = 0, [], []

    def read():
        nonlocal index
        scan = scans[index]
        index += 1
        if isinstance(scan, Exception):
            raise scan
        return scan

    def launch():
        starts.append(index)
        if fail_launch:
            raise OSError('Fixture: launch unavailable')

    def wait(seconds):
        assert seconds == 1
        return index == len(scans)

    follow_codex(read, launch, wait, errors.append)
    return starts, errors


assert exercise([set(), set(), set()]) == ([], [])
# Already-open Codex starts immediately. Remaining open does not bring a
# manually closed pet back; hiding/reopening the same process starts it again.
starts, errors = exercise([{101}, {101}, OSError('Fixture: scan failed'), {101}, set(), {101}, {202}])
assert starts == [1, 6, 7] and len(errors) == 1
# Several windows for one process are deduplicated; a batch of new processes
# still launches just one singleton companion.
assert exercise([{101, 101}, {101}, {101, 202, 303}, {101, 202, 303}]) == ([1, 3], [])
starts, errors = exercise([{101}, {101}, {101}], fail_launch=True)
assert starts == [1] and len(errors) == 1
print('PASS: Codex package/CLI distinction; idle/already-open/reopen transitions; no respawn while continuously open; failed scans do not retrigger; batches deduplicate; launch errors do not loop')

if '--native-probe' in sys.argv:
    from pet_autostart import visible_codex_processes
    processes = visible_codex_processes()
    assert processes, 'Open the Codex desktop window before the native probe'
    print(f'PASS: actual visible Codex desktop detected ({len(processes)} process)')
