import os
import tempfile
import zipfile
from jinja2 import Environment, FileSystemLoader

def test_compile_zip_external_attr():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "output.zip")
        template_dir = os.path.join(tmpdir, "templates")
        os.makedirs(template_dir)
        
        # 创建模板文件
        with open(os.path.join(template_dir, "hello.html"), "w") as f:
            f.write("Hello {{ name }}!")
        
        env = Environment(loader=FileSystemLoader(template_dir))
        
        # 调用 compile_templates 生成 zip
        env.compile_templates(target, zip="deflated", ignore_errors=False)
        
        # 检查 zip 内容
        with zipfile.ZipFile(target, "r") as zf:
            # 获取 zip 中的文件名（可能包含路径）
            names = zf.namelist()
            assert len(names) == 1, f"Expected 1 file, got {names}"
            
            # 获取文件信息
            info = zf.getinfo(names[0])
            # 原代码: 0o755 << 16 = 0o7550000 (十进制 25165824)
            # 变异代码: 0o755 >> 16 = 0 (十进制 0)
            assert info.external_attr == 0o755 << 16, f"external_attr = {info.external_attr}"
