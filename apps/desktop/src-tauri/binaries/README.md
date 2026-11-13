# sidecar 二进制放这里

打包前运行 `scripts/build-sidecar.ps1`，它会用 PyInstaller 把 backend 打成
`info-station-backend-x86_64-pc-windows-msvc.exe` 并复制到本目录（文件名必须带目标三元组）。
这些 exe 不进 Git。
