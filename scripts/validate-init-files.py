#!/usr/bin/env python3
"""
Valida a estrutura de arquivos __init__.py conforme diretrizes de codificação.

Este script verifica que:
1. Pacotes-base da arquitetura (domain/entities, domain/repositories, etc) têm __init__.py
2. Subpastas de features (ente/, descoberta/, contabil/, etc) NÃO têm __init__.py
3. Diretórios intermediários (domain/, application/, infrastructure/, interfaces/) NÃO têm __init__.py

Baseado em: docs/architecture/coding_guidelines.md
"""

import sys
from pathlib import Path
from typing import List


# Pacotes-base que DEVEM ter __init__.py
REQUIRED_BASE_PACKAGES = [
    "src/domain/entities",
    "src/domain/repositories",
    "src/domain/value_objects",
    "src/domain/services",
    "src/domain/gateways",
    "src/application/use_cases",
    "src/application/services",
    "src/infrastructure/db/repositories",
    "src/infrastructure/db/services",
    "src/infrastructure/gateways",
    "src/infrastructure/messaging",
    # Para projetos shared-*
    "src/*/domain/entities",
    "src/*/domain/repositories",
    "src/*/domain/value_objects",
    "src/*/domain/services",
    "src/*/domain/gateways",
    "src/*/infrastructure/db/repositories",
    "src/*/infrastructure/db/services",
]

# Padrões de subpastas de features que NÃO DEVEM ter __init__.py
FORBIDDEN_FEATURE_PATTERNS = [
    "*/domain/entities/*/__init__.py",
    "*/domain/repositories/*/__init__.py",
    "*/domain/value_objects/*/__init__.py",
    "*/domain/services/*/__init__.py",
    "*/domain/gateways/*/__init__.py",
    "*/application/use_cases/*/__init__.py",
    "*/application/services/*/__init__.py",
    "*/infrastructure/db/repositories/*/__init__.py",
    "*/infrastructure/db/services/*/__init__.py",
    "*/infrastructure/gateways/*/__init__.py",
    "*/infrastructure/messaging/*/__init__.py",
    "*/tests/unit/*/__init__.py",
    "*/tests/integration/*/__init__.py",
]


def find_project_root() -> Path:
    """Encontra o diretório raiz do projeto."""
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    return project_root


def find_missing_base_packages(project_root: Path) -> List[str]:
    """Encontra pacotes-base que deveriam ter __init__.py mas não têm."""
    missing = []

    for pattern in REQUIRED_BASE_PACKAGES:
        # Expandir padrões com * (para projetos shared-*)
        if "*" in pattern:
            base_path = project_root / pattern.split("*")[0].rstrip("/")
            if base_path.exists():
                for subdir in base_path.iterdir():
                    if subdir.is_dir():
                        full_pattern = pattern.replace("*", subdir.name)
                        check_path = project_root / full_pattern
                        if check_path.exists() and not (check_path / "__init__.py").exists():
                            missing.append(
                                str(check_path.relative_to(project_root) / "__init__.py")
                            )
        else:
            check_path = project_root / pattern
            if check_path.exists() and not (check_path / "__init__.py").exists():
                missing.append(str(check_path.relative_to(project_root) / "__init__.py"))

    return missing


def find_forbidden_init_files(project_root: Path) -> List[str]:
    """Encontra __init__.py em subpastas de features (indevidos)."""
    forbidden = []

    for pattern in FORBIDDEN_FEATURE_PATTERNS:
        for init_file in project_root.glob(pattern):
            # Verificar se é realmente uma subpasta de feature (não é o pacote-base)
            # Ex: domain/entities/descoberta/__init__.py é indevido
            # Mas domain/entities/__init__.py é válido

            # Ignorar se está no pacote-base
            if any(
                base_pkg in str(init_file.parent.parent)
                for base_pkg in [
                    "domain/entities",
                    "domain/repositories",
                    "domain/value_objects",
                    "domain/services",
                    "domain/gateways",
                    "application/use_cases",
                    "application/services",
                    "infrastructure/db/repositories",
                    "infrastructure/db/services",
                    "infrastructure/gateways",
                    "infrastructure/messaging",
                ]
            ):
                # É uma subpasta de feature
                forbidden.append(str(init_file.relative_to(project_root)))

    return forbidden


def find_intermediate_dir_init_files(project_root: Path) -> List[str]:
    """Encontra __init__.py em diretórios intermediários (indevidos).

    Diretórios intermediários são diretórios de organização estrutural
    que NÃO devem ter __init__.py, como:
    - src/domain/__init__.py
    - src/application/__init__.py
    - src/infrastructure/__init__.py
    - src/interfaces/__init__.py
    """
    forbidden = []

    # Padrões de diretórios intermediários
    intermediate_patterns = [
        "src/domain/__init__.py",
        "src/application/__init__.py",
        "src/infrastructure/__init__.py",
        "src/interfaces/__init__.py",
        # Para projetos shared-* (ex: src/shared_core/domain/__init__.py)
        "src/*/domain/__init__.py",
        "src/*/application/__init__.py",
        "src/*/infrastructure/__init__.py",
        "src/*/interfaces/__init__.py",
    ]

    for pattern in intermediate_patterns:
        for init_file in project_root.glob(pattern):
            if init_file.exists():
                forbidden.append(str(init_file.relative_to(project_root)))

    return forbidden


def main() -> int:
    """Executa a validação."""
    project_root = find_project_root()

    print(f"Validando estrutura de __init__.py em: {project_root}")
    print()

    # Verificar pacotes-base faltando __init__.py
    missing = find_missing_base_packages(project_root)

    # Verificar __init__.py indevidos em subpastas de features
    forbidden_features = find_forbidden_init_files(project_root)

    # Verificar __init__.py indevidos em diretórios intermediários
    forbidden_intermediate = find_intermediate_dir_init_files(project_root)

    has_errors = False

    if missing:
        has_errors = True
        print("❌ PACOTES-BASE SEM __init__.py (obrigatório):")
        for path in sorted(missing):
            print(f"   - {path}")
        print()

    if forbidden_intermediate:
        has_errors = True
        print("❌ DIRETÓRIOS INTERMEDIÁRIOS COM __init__.py (indevido):")
        for path in sorted(forbidden_intermediate):
            print(f"   - {path}")
        print()

    if forbidden_features:
        has_errors = True
        print("❌ SUBPASTAS DE FEATURES COM __init__.py (indevido):")
        for path in sorted(forbidden_features):
            print(f"   - {path}")
        print()

    if has_errors:
        print("💡 Diretrizes:")
        print("   - Pacotes-base (domain/entities, domain/repositories, etc) DEVEM ter __init__.py")
        print(
            "   - Diretórios intermediários (domain/, application/, etc) NÃO DEVEM ter __init__.py"
        )
        print("   - Subpastas de features (ente/, descoberta/, etc) NÃO DEVEM ter __init__.py")
        print()
        print("📖 Ver: docs/architecture/coding_guidelines.md")
        return 1

    print("✅ Estrutura de __init__.py está conforme as diretrizes!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
