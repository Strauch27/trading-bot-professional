#!/usr/bin/env python3
"""
Dependency Analyzer
Analyzes function call dependencies and checks for consistency
"""

import ast
import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple


class DependencyAnalyzer(ast.NodeVisitor):
    """AST visitor to extract function calls and dependencies"""

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.functions = {}  # function_name -> {calls: set(), called_by: set()}
        self.current_function = None
        self.external_calls = defaultdict(set)  # function_name -> set of external calls

    def visit_FunctionDef(self, node):
        """Track function definitions"""
        self.current_function = node.name
        if node.name not in self.functions:
            self.functions[node.name] = {'calls': set(), 'called_by': set(), 'line': node.lineno}
        self.generic_visit(node)
        self.current_function = None

    def visit_Call(self, node):
        """Track function calls"""
        if self.current_function:
            # Try to extract function name
            func_name = None

            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            elif isinstance(node.func, ast.Call):
                # Chained call, skip
                pass

            if func_name:
                # Check if it's an internal call
                if func_name in self.functions or func_name.startswith('_'):
                    self.functions[self.current_function]['calls'].add(func_name)

                    if func_name in self.functions:
                        self.functions[func_name]['called_by'].add(self.current_function)
                else:
                    # External call
                    self.external_calls[self.current_function].add(func_name)

        self.generic_visit(node)


def analyze_file(filepath: str) -> DependencyAnalyzer:
    """Analyze a single Python file"""
    with open(filepath, 'r') as f:
        try:
            tree = ast.parse(f.read(), filename=filepath)
            analyzer = DependencyAnalyzer(filepath)
            analyzer.visit(tree)
            return analyzer
        except SyntaxError as e:
            print(f"⚠️  Syntax error in {filepath}: {e}")
            return None


def find_unused_functions(analyzer: DependencyAnalyzer) -> List[str]:
    """Find functions that are never called"""
    unused = []
    for func_name, info in analyzer.functions.items():
        if not info['called_by'] and not func_name.startswith('__'):
            # Not called by anyone and not a magic method
            unused.append(func_name)
    return unused


def find_circular_dependencies(analyzer: DependencyAnalyzer) -> List[Tuple[str, str]]:
    """Find circular function dependencies"""
    circular = []
    for func_a, info_a in analyzer.functions.items():
        for func_b in info_a['calls']:
            if func_b in analyzer.functions:
                info_b = analyzer.functions[func_b]
                if func_a in info_b['calls']:
                    # A calls B and B calls A
                    circular.append((func_a, func_b))
    return circular


def main():
    """Main analysis"""
    print("=" * 80)
    print("DEPENDENCY ANALYZER")
    print("=" * 80)
    print()

    # Analyze critical files
    critical_files = [
        'engine/buy_decision.py',
        'core/portfolio/portfolio.py',
        'services/exits.py',
        'services/market_data.py',
        'ui/dashboard.py',
        'engine/position_manager.py',
    ]

    print("Analyzing files...")
    print()

    for filepath in critical_files:
        if not os.path.exists(filepath):
            print(f"⚠️  {filepath} not found")
            continue

        print(f"📄 {filepath}")
        analyzer = analyze_file(filepath)

        if not analyzer:
            continue

        print(f"   Functions: {len(analyzer.functions)}")

        # Find unused functions
        unused = find_unused_functions(analyzer)
        if unused:
            print(f"   ⚠️  Unused functions ({len(unused)}): {', '.join(unused[:5])}")

        # Find circular dependencies
        circular = find_circular_dependencies(analyzer)
        if circular:
            print(f"   ⚠️  Circular dependencies: {circular[:3]}")

        # Find complex dependencies (function calls >10 other functions)
        complex_funcs = [
            (name, len(info['calls']))
            for name, info in analyzer.functions.items()
            if len(info['calls']) > 10
        ]
        if complex_funcs:
            complex_funcs.sort(key=lambda x: x[1], reverse=True)
            top = complex_funcs[0]
            print(f"   ⚠️  High fan-out: {top[0]} calls {top[1]} functions")

        print()

    print("=" * 80)
    print("Analysis complete")
    print()


if __name__ == "__main__":
    main()
