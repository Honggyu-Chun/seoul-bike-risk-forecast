"""Download pinned development assets and install only verified, missing files."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = ('data/processed/', 'artifacts/models/', 'artifacts/evaluation/',
           'artifacts/followup_v4_20261020/data/', 'artifacts/followup_v4_20261020/A/models/', 'deliverables/')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def destination(root, name):
    relative = PurePosixPath(name)
    if ('\\' in name or ':' in name or relative.is_absolute() or '..' in relative.parts
            or not name.startswith(ALLOWED)):
        raise ValueError('허용하지 않는 파일 경로를 거부한다.')
    target = root.joinpath(*relative.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('프로젝트 밖으로 향하는 경로를 거부한다.')
    return target


def file_matches(path, item):
    return path.is_file() and path.stat().st_size == item['bytes'] and digest(path) == item['sha256']


def check_profile(root, profile):
    return [item['path'] for item in profile['files']
            if not file_matches(destination(root, item['path']), item)]


def verify_archive(archive, profile):
    spec = profile['archive']
    if archive.stat().st_size != spec['bytes'] or digest(archive) != spec['sha256']:
        raise ValueError('압축 파일의 크기 또는 SHA-256이 안내된 버전과 다르다.')


def install_archive(root, archive, profile):
    root = root.resolve()
    verify_archive(archive, profile)
    expected = {item['path']: item for item in profile['files']}
    if len(expected) != len(profile['files']):
        raise ValueError('설정에 중복 파일이 있다.')
    for name, item in expected.items():
        target = destination(root, name)
        if target.exists() and not file_matches(target, item):
            raise FileExistsError(f'기존 파일을 보존한다: {name}. 새 클론 폴더에서 준비하거나 버전을 먼저 확인한다.')
    with zipfile.ZipFile(archive) as source, tempfile.TemporaryDirectory(prefix='bike-runtime-') as temp:
        members = source.infolist()
        names = [item.filename for item in members]
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError('압축 내부 파일 목록이 설정과 다르다.')
        staging = Path(temp)
        # Validate every member before writing anything into the project.
        for member in members:
            destination(root, member.filename)
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError('심볼릭 링크를 거부한다.')
            item = expected[member.filename]
            if member.file_size != item['bytes']:
                raise ValueError('압축 내부 파일 크기가 다르다.')
            staged = staging.joinpath(*PurePosixPath(member.filename).parts)
            staged.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as incoming, staged.open('wb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            if not file_matches(staged, item):
                raise ValueError('압축 내부 파일 해시가 다르다.')
        installed = 0
        for name, item in expected.items():
            target = destination(root, name)
            if file_matches(target, item):
                continue
            if target.exists():
                raise FileExistsError(f'다른 작업이 만든 파일을 보존한다: {name}')
            target.parent.mkdir(parents=True, exist_ok=True)
            # The temporary copy is on the target volume; replace is atomic.
            fd, temporary = tempfile.mkstemp(prefix=target.name+'.', suffix='.tmp', dir=target.parent)
            os.close(fd)
            try:
                shutil.copyfile(staging.joinpath(*PurePosixPath(name).parts), temporary)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
            installed += 1
    if check_profile(root, profile):
        raise ValueError('설치 후 파일 검증에 실패한다.')
    return installed


def download(profile, cache):
    spec = profile['archive']
    path = cache / spec['name']
    if path.exists():
        verify_archive(path, profile)
        return path
    if not spec['url'].startswith('https://github.com/Honggyu-Chun/seoul-bike-risk-forecast/releases/download/'):
        raise ValueError('등록한 저장소의 HTTPS 배포 주소만 허용한다.')
    cache.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix+'.part')
    print(f"내려받기: {spec['name']} ({spec['bytes']/1_000_000:.1f} MB)", flush=True)
    request = Request(spec['url'], headers={'User-Agent':'SeoulBikeRiskForecast-TeamSetup/1.0'})
    total = 0
    with urlopen(request, timeout=60) as response, partial.open('wb') as output:
        while chunk := response.read(1024*1024):
            total += len(chunk)
            if total > spec['bytes']:
                raise ValueError('예상 크기를 넘는 응답을 거부한다.')
            output.write(chunk)
    verify_archive(partial, profile)
    partial.replace(path)
    return path


def profile_order(profiles, selected, visiting=None):
    visiting = set() if visiting is None else visiting
    if selected in visiting:
        raise ValueError('준비 프로필 의존 관계에 순환이 있다.')
    visiting.add(selected)
    order = []
    for dependency in profiles[selected].get('depends_on', []):
        order.extend(profile_order(profiles, dependency, visiting))
    visiting.remove(selected)
    return list(dict.fromkeys(order+[selected]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['demo','research'], default='demo')
    parser.add_argument('--check', action='store_true', help='다운로드 없이 파일 상태만 확인한다.')
    parser.add_argument('--archive', type=Path, help='선택 프로필의 ZIP을 직접 지정한다.')
    args = parser.parse_args()
    profiles = json.loads((ROOT/'config/team_runtime.json').read_text(encoding='utf-8'))['profiles']
    for name in profile_order(profiles, args.profile):
        profile = profiles[name]
        missing = check_profile(ROOT, profile)
        if not missing:
            print(f'{name}: 준비 완료 ({len(profile["files"])}개 파일 검증)', flush=True)
            continue
        if args.check:
            print(f'{name}: 준비가 필요한 파일 {len(missing)}개', flush=True)
            for path in missing:print('  '+path)
            return 1
        archive = args.archive.resolve() if name == args.profile and args.archive else download(profile, ROOT/'data/runtime_cache')
        count = install_archive(ROOT, archive, profile)
        print(f'{name}: {count}개 파일 준비, 전체 해시 검증 완료', flush=True)
    print('재학습 없이 기존 1시간 시제품을 실행할 수 있다.', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print('준비 실패: '+str(error))
        print('네트워크를 확인하거나 안내된 ZIP을 직접 내려받아 --archive 경로로 다시 실행한다.')
        raise SystemExit(1)
