#!/usr/bin/env pwsh
# 汉字字体压缩器 PowerShell 启动脚本
# 使用方法: .\run_hfc.ps1 --demo --route all --output report.html

$env:PYTHONPATH = "$PSScriptRoot/src"
python -m hfc.cli $args