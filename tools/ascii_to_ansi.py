#!/usr/bin/env python3
"""
Конвертер ASCII → ANSI.
- Заменяет Braille (⣿⣷ и т.п.) на ASCII/блочные символы по плотности
- Добавляет цветовые коды для красивого вывода в терминале.

Использование:
  python tools/ascii_to_ansi.py art.txt
  python tools/ascii_to_ansi.py art.txt -o art_ansi.txt
  python tools/ascii_to_ansi.py art.txt --braille ascii  # замена Braille на #
  echo "Hello" | python tools/ascii_to_ansi.py
"""
import argparse
import sys
from pathlib import Path

# ANSI escape
RESET = "\033[0m"

# Braille → символ по плотности точек (0-8)
# ascii: только ASCII, работает везде
BRAILLE_TO_ASCII = [" ", ".", ":", "*", "O", "@", "#", "#", "#"]
# block: ░▒▓█ — плотнее, но нужна поддержка Unicode
BRAILLE_TO_BLOCK = [" ", "\u2591", "\u2592", "\u2593", "\u2588", "\u2588", "\u2588", "\u2588", "\u2588"]


def braille_dots(c: str) -> int:
    """Количество заполненных точек в символе Braille (0-8)."""
    if not ("\u2800" <= c <= "\u28ff"):
        return -1
    bits = ord(c) - 0x2800
    return bin(bits).count("1")


def braille_to_chars(text: str, style: str = "ascii") -> str:
    """Заменяет Braille (⣿⣷ и т.п.) на символы по плотности. style: ascii | block."""
    table = BRAILLE_TO_ASCII if style == "ascii" else BRAILLE_TO_BLOCK
    result = []
    for c in text:
        d = braille_dots(c)
        if d >= 0:
            # 6-dot Braille: max 6, 8-dot: max 8. Нормализуем на 0-8
            idx = min(d, 8)
            result.append(table[idx])
        else:
            result.append(c)
    return "".join(result)


def ansi_fg_256(n: int) -> str:
    """Цвет текста из 256-цветной палитры."""
    return f"\033[38;5;{n}m"


def ansi_bright_fg(n: int) -> str:
    """Яркий цвет (0-7: black, red, green, yellow, blue, magenta, cyan, white)."""
    return f"\033[9{n}m" if 0 <= n <= 7 else RESET


# Палитры (индексы 256-цветной схемы)
PALETTE_CYAN = [51, 87, 123, 159, 195, 231]  # голубой градиент
PALETTE_RAINBOW = [196, 208, 226, 46, 51, 21, 129, 201]  # радуга
PALETTE_VAPORWAVE = [201, 213, 219, 51, 45, 39]  # vaporwave
PALETTE_FIRE = [196, 202, 208, 214, 220, 226]  # огонь
PALETTE_OCEAN = [17, 18, 19, 20, 21, 27, 33, 39, 45, 51]  # океан


def convert_gradient(text: str, palette: list[int] | None = None) -> str:
    """Горизонтальный градиент по строкам."""
    palette = palette or PALETTE_CYAN
    lines = text.splitlines()
    result = []
    for i, line in enumerate(lines):
        if not line.strip():
            result.append(line)
            continue
        color_idx = (i * len(palette)) // max(1, len(lines)) % len(palette)
        color = ansi_fg_256(palette[color_idx])
        result.append(f"{color}{line}{RESET}")
    return "\n".join(result)


def convert_char_gradient(text: str, palette: list[int] | None = None) -> str:
    """Градиент по символам (слева направо)."""
    palette = palette or PALETTE_CYAN
    lines = text.splitlines()
    result = []
    max_len = max(len(l) for l in lines) if lines else 0
    for line in lines:
        if not line.strip():
            result.append(line)
            continue
        colored = []
        for j, c in enumerate(line):
            if c.isspace():
                colored.append(c)
            else:
                idx = (j * len(palette)) // max(1, max_len) % len(palette)
                colored.append(f"{ansi_fg_256(palette[idx])}{c}{RESET}")
        result.append("".join(colored))
    return "\n".join(result)


def convert_char_based(text: str) -> str:
    """Цвет по типу символа: плотные (#@) — ярче, редкие (.) — тусклее."""
    # Плотность символов для ASCII-арта
    dense = "#@%&*"
    medium = "+=:oO0"
    light = ".,'` "
    lines = text.splitlines()
    result = []
    for line in lines:
        colored = []
        for c in line:
            if c in dense:
                colored.append(f"{ansi_fg_256(51)}{c}{RESET}")  # cyan bright
            elif c in medium:
                colored.append(f"{ansi_fg_256(87)}{c}{RESET}")  # cyan
            elif c in light:
                colored.append(f"{ansi_fg_256(246)}{c}{RESET}")  # gray
            else:
                colored.append(f"{ansi_fg_256(195)}{c}{RESET}")  # light cyan
        result.append("".join(colored))
    return "\n".join(result)


def convert_palette_cycle(text: str, palette: list[int] | None = None) -> str:
    """Цикл по палитре для каждого символа."""
    palette = palette or PALETTE_RAINBOW
    lines = text.splitlines()
    result = []
    idx = 0
    for line in lines:
        colored = []
        for c in line:
            if c.isspace():
                colored.append(c)
            else:
                colored.append(f"{ansi_fg_256(palette[idx % len(palette)])}{c}{RESET}")
                idx += 1
        result.append("".join(colored))
    return "\n".join(result)


def convert_line_cycle(text: str, palette: list[int] | None = None) -> str:
    """Каждая строка — свой цвет из палитры."""
    palette = palette or PALETTE_CYAN
    lines = text.splitlines()
    result = []
    for i, line in enumerate(lines):
        color = ansi_fg_256(palette[i % len(palette)])
        result.append(f"{color}{line}{RESET}")
    return "\n".join(result)


MODES = {
    "gradient": convert_gradient,
    "char": convert_char_gradient,
    "char-based": convert_char_based,
    "palette": convert_palette_cycle,
    "line": convert_line_cycle,
}

PALETTES = {
    "cyan": PALETTE_CYAN,
    "rainbow": PALETTE_RAINBOW,
    "vaporwave": PALETTE_VAPORWAVE,
    "fire": PALETTE_FIRE,
    "ocean": PALETTE_OCEAN,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="ASCII → ANSI конвертер")
    parser.add_argument("input", nargs="?", help="Файл с ASCII-артом (или stdin)")
    parser.add_argument("-o", "--output", help="Файл для вывода (иначе stdout)")
    parser.add_argument("-m", "--mode", choices=list(MODES), default="gradient", help="Режим раскраски")
    parser.add_argument("-p", "--palette", choices=list(PALETTES), default="cyan", help="Палитра цветов")
    parser.add_argument(
        "--braille",
        choices=["ascii", "block"],
        help="Заменять Braille (⣿) на символы по плотности: ascii (#*.:) или block (█▓▒░)",
    )
    args = parser.parse_args()

    if args.input:
        path = Path(args.input)
        if not path.exists():
            print(f"Файл не найден: {path}", file=sys.stderr)
            sys.exit(1)
        text = path.read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    if args.braille:
        text = braille_to_chars(text, style=args.braille)

    palette = PALETTES.get(args.palette)
    converter = MODES[args.mode]
    if args.mode == "char-based":
        result = converter(text)
    else:
        result = converter(text, palette)

    if args.output:
        Path(args.output).write_text(result, encoding="utf-8")
        print(f"Сохранено в {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
