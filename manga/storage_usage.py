"""要求時だけ容量を計測する。画像・履歴を削除・圧縮しない。"""

import os
from pathlib import Path


def project_usage(directory, project_id):
    directory = Path(directory)
    result = dict(json_bytes=0, image_bytes=0, other_bytes=0, files=0, warnings=[])
    def add(path, stat):
        key = "json_bytes" if path.suffix.lower()==".json" else "image_bytes" if path.suffix.lower() in (".png",".jpg",".jpeg",".webp") else "other_bytes"
        result[key] += stat.st_size
        result["files"] += 1
    project = directory / f"{project_id}.json"
    if project.is_file() and not project.is_symlink(): add(project,project.stat())
    def visit(root):
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        result["warnings"].append(f"シンボリックリンクを計測から除外：{entry.name}")
                    elif entry.is_dir(follow_symlinks=False):visit(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):add(Path(entry.path),entry.stat(follow_symlinks=False))
        except FileNotFoundError:pass
        except OSError as error:result["warnings"].append(f"計測できないフォルダ：{root.name}（{error.strerror}）")
    root = directory / project_id
    if not root.is_symlink():visit(root)
    result["total_bytes"] = sum(result[key] for key in ("json_bytes","image_bytes","other_bytes"))
    return result
