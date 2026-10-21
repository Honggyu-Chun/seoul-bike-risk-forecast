import hashlib
from pathlib import Path
import zipfile
import pytest
from setup_runtime import install_archive, destination, check_profile


def bundle(tmp_path, name='artifacts/models/test.txt', payload=b'model', expected=None):
    archive = tmp_path/'runtime.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr(name,payload)
    profile={'archive':{'bytes':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()},
             'files':[{'path':name,'bytes':len(payload),'sha256':hashlib.sha256(payload if expected is None else expected).hexdigest()}]}
    return archive,profile


def test_verified_install_is_repeatable_and_detects_changed_existing_file(tmp_path):
    archive,p=bundle(tmp_path);root=tmp_path/'project';root.mkdir()
    assert install_archive(root,archive,p)==1
    assert check_profile(root,p)==[]
    assert install_archive(root,archive,p)==0
    target=root/p['files'][0]['path'];target.write_bytes(b'modified')
    with pytest.raises(FileExistsError):install_archive(root,archive,p)
    assert target.read_bytes()==b'modified'


@pytest.mark.parametrize('path',['../escape','/absolute','C:/escape','artifacts/models/../../escape','data/processed/..\\escape','.env'])
def test_paths_cannot_escape_or_write_unapproved_locations(tmp_path,path):
    with pytest.raises(ValueError):destination(tmp_path,path)


def test_bad_member_hash_installs_nothing(tmp_path):
    archive,p=bundle(tmp_path,expected=b'wrong');root=tmp_path/'project';root.mkdir()
    with pytest.raises(ValueError,match='해시'):install_archive(root,archive,p)
    assert not list(root.rglob('test.txt'))


def test_extra_archive_member_is_rejected_before_install(tmp_path):
    archive,p=bundle(tmp_path)
    with zipfile.ZipFile(archive,'a') as z:z.writestr('artifacts/models/unexpected.txt',b'no')
    p['archive']={'bytes':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
    root=tmp_path/'project';root.mkdir()
    with pytest.raises(ValueError,match='목록'):install_archive(root,archive,p)
    assert not list(root.rglob('*.txt'))


def test_changed_archive_is_rejected(tmp_path):
    archive,p=bundle(tmp_path);archive.write_bytes(archive.read_bytes()+b'changed')
    root=tmp_path/'project';root.mkdir()
    with pytest.raises(ValueError,match='SHA-256'):install_archive(root,archive,p)
