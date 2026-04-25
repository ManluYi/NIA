#!/usr/bin/env python3
"""
统一标准化所有 .smt2 文件：
- 删除末尾所有可能的命令块（check-sat, get-objectives, exit, set-option ...）
- 确保有一个优化目标（minimize/maximize），使用第一个整数变量
- 添加超时设置 (set-option :timeout 1200000)
- 添加 (check-sat) (get-objectives) (exit)
用法: python unify_opt.py <目录> [--max] [--dry-run]
"""

import re
import sys
from pathlib import Path

def extract_first_int_var(content):
    """提取第一个声明的整数变量名"""
    # 匹配 (declare-fun var () Int)
    match = re.search(r'\(declare-fun\s+(\w+)\s*\(\)\s+Int\)', content)
    if match:
        return match.group(1)
    # 如果找不到，尝试更宽松的匹配
    match2 = re.search(r'\(declare-fun\s+(\w+)\s', content)
    if match2:
        return match2.group(1)
    return None

def has_optimization(content):
    """检查是否已存在优化命令 (minimize 或 maximize)"""
    return bool(re.search(r'\(minimize\s+', content) or re.search(r'\(maximize\s+', content))

def get_existing_opt(content):
    """如果已有优化命令，返回 (type, var)，否则 (None, None)"""
    match = re.search(r'\((minimize|maximize)\s+(\w+)\)', content)
    if match:
        return match.group(1), match.group(2)
    return None, None

def clean_end(lines):
    """从末尾删除所有命令行（check-sat, get-objectives, exit, set-option ...）及空行"""
    commands = {'(check-sat)', '(get-objectives)', '(exit)'}
    # 从后向前遍历，删除行
    while lines:
        line = lines[-1].strip()
        if line == '':
            lines.pop()
            continue
        if line in commands or line.startswith('(set-option :timeout'):
            lines.pop()
        else:
            break
    # 再删除末尾可能残留的空行
    while lines and lines[-1].strip() == '':
        lines.pop()
    return lines

def process_file(filepath, opt_type, dry_run):
    with open(filepath, 'r') as f:
        content = f.read()

    lines = content.split('\n')
    # 1. 清理末尾的命令块
    lines = clean_end(lines)

    # 2. 确定优化变量和类型
    existing_type, existing_var = get_existing_opt(content)
    if existing_var:
        opt_var = existing_var
        opt_type = existing_type  # 保持原有优化方向
    else:
        opt_var = extract_first_int_var(content)
        if not opt_var:
            print(f"跳过 {filepath}（未找到整数变量）")
            return

    # 3. 构建新内容
    new_lines = lines[:]
    # 如果没有优化命令，则添加
    if not existing_var:
        new_lines.append(f"({opt_type} {opt_var})")
    # 添加超时设置
    new_lines.append("(set-option :timeout 1200000)")
    # 添加标准命令块
    new_lines.append("(check-sat)")
    new_lines.append("(get-objectives)")
    new_lines.append("(exit)")
    new_content = '\n'.join(new_lines) + '\n'

    if dry_run:
        print(f"预览 {filepath}")
        print("末尾内容预览：")
        print('\n'.join(new_lines[-8:]))
        return

    # 直接覆盖原文件，不生成备份
    with open(filepath, 'w') as f:
        f.write(new_content)
    print(f"已处理 {filepath}")

def main():
    if len(sys.argv) < 2:
        print("用法: python unify_opt.py <目录> [--max] [--dry-run]")
        sys.exit(1)
    root = Path(sys.argv[1])
    if not root.is_dir():
        print(f"错误：{root} 不是目录")
        sys.exit(1)
    opt_type = 'maximize' if '--max' in sys.argv else 'minimize'
    dry_run = '--dry-run' in sys.argv
    for smt in root.glob('**/*.smt2'):
        process_file(smt, opt_type, dry_run)

if __name__ == '__main__':
    main()