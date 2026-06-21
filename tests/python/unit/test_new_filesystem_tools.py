import pytest
from pathlib import Path
from aja.orchestration.tools.native import NativeToolRegistry


def test_list_directory(tmp_path):
    registry = NativeToolRegistry()
    
    # Create test directories and files
    dir_path = tmp_path / "test_dir"
    dir_path.mkdir()
    (dir_path / "sub_dir").mkdir()
    (dir_path / "file1.txt").write_text("hello", encoding="utf-8")
    (dir_path / "file2.txt").write_text("world", encoding="utf-8")
    
    res = registry.execute("list_directory", {"path": str(dir_path)})
    
    assert "[DIR]  sub_dir" in res
    assert "[FILE] file1.txt (5 bytes)" in res
    assert "[FILE] file2.txt (5 bytes)" in res


def test_find_files(tmp_path):
    registry = NativeToolRegistry()
    
    dir_path = tmp_path / "test_find"
    dir_path.mkdir()
    sub_dir = dir_path / "sub"
    sub_dir.mkdir()
    
    (dir_path / "test1.py").write_text("print(1)", encoding="utf-8")
    (sub_dir / "test2.py").write_text("print(2)", encoding="utf-8")
    (dir_path / "doc.txt").write_text("text", encoding="utf-8")
    
    res = registry.execute("find_files", {"path": str(dir_path), "pattern": "*.py"})
    
    assert "test1.py (8 bytes)" in res
    assert "sub\\test2.py (8 bytes)" in res or "sub/test2.py (8 bytes)" in res
    assert "doc.txt" not in res


def test_get_file_info(tmp_path):
    registry = NativeToolRegistry()
    
    file_path = tmp_path / "info.txt"
    file_path.write_text("some content", encoding="utf-8")
    
    res = registry.execute("get_file_info", {"path": str(file_path)})
    
    assert "Type: File" in res
    assert "info.txt" in res
    assert "Size: 12 bytes" in res
    
    res_dir = registry.execute("get_file_info", {"path": str(tmp_path)})
    assert "Type: Directory" in res_dir


def test_create_directory(tmp_path):
    registry = NativeToolRegistry()
    
    new_dir = tmp_path / "new" / "nested" / "dir"
    res = registry.execute("create_directory", {"path": str(new_dir)})
    
    assert "Successfully created directory" in res
    assert new_dir.exists()
    assert new_dir.is_dir()
