# 独立可执行版本构建

此目录为课程提交包的启动入口和构建脚本，不会读取或打包项目根目录的 `.env`。默认启动即为离线 Mock 演示，不需要安装 Python 或填写 API Key。真实模型接入请使用源码版的 README。

## 在目标系统构建

Windows 的 `.exe` 必须在 Windows 上构建；macOS Apple 芯片与 Intel 芯片也分别使用对应架构的 Python 构建。推荐 Python 3.11。

```bash
python -m pip install -r packaging/requirements-build.txt
python packaging/build_app.py --dist-dir dist --work-dir build/pyinstaller
```

输出目录 `dist/FGDocQA/` 必须整体保留：

- Windows：`FGDocQA.exe`、`启动FGDocQA.bat`、`_internal/`。
- macOS：`FGDocQA`、`启动FGDocQA.command`、`_internal/`。
- 两个平台均包含使用说明、构建信息和素材署名。

构建脚本同时生成 `FGDocQA-windows-x64.zip` 或 `FGDocQA-macos-arm64.tar.gz` / `FGDocQA-macos-x64.tar.gz`。macOS 使用 tar.gz 保留框架符号链接与可执行权限。

## 验证

Windows 执行 `dist/FGDocQA/FGDocQA.exe --smoke-test`；macOS 执行：

```bash
dist/FGDocQA/FGDocQA --smoke-test
```

自检临时解析示例、建立 Mock 索引，并实际验证本机 HTTP 健康接口、前端和鲸鱼娘图片、带引用问答、文档图片预览。完成后关闭服务并清理临时数据。

启动器回归检查：

```bash
PYTHONPATH=src python3 -m unittest discover -s packaging -p 'test_*.py' -v
```

## 使用方式

完整解压后双击平台启动文件，或运行 `FGDocQA --help` 查看参数。默认数据目录为 Windows 的 `%LOCALAPPDATA%\FGDocQA\demo-v1`、macOS 的 `~/Library/Application Support/FGDocQA/demo-v1`。可用 `--data-dir` 指定另一个专用演示目录。

优先监听 `127.0.0.1:8770`，已被占用时自动使用其他空闲端口。控制台显示实际网址，自动打开浏览器；关闭控制台窗口或按 Ctrl+C 停止。`--no-browser` 可禁止自动打开浏览器，`--port 0` 可直接要求系统分配端口。

`--cli` 可调用源码版的命令行工具，例如 `FGDocQA --cli query "问题"`。CLI 的当前目录是演示工作区，自有输入文档请使用绝对路径。此入口也始终保持 Mock。
