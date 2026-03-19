# ASCII → ANSI конвертер

- **Braille → ASCII**: заменяет ⣿⣷ и т.п. на # * : . по плотности точек
- Добавляет цветовые коды к арту

## Использование

```bash
# Braille → ASCII + цвета (для art.txt с Braille)
python tools/ascii_to_ansi.py art.txt --braille ascii -o art_ansi.txt

# Только замена Braille (без цветов): --braille ascii | block
# block = █▓▒░ (плотнее, но нужна поддержка Unicode)

# Обычный ASCII-арт
python tools/ascii_to_ansi.py art_ascii.txt -o art_ansi.txt

# Режимы: gradient, char, char-based, palette, line
python tools/ascii_to_ansi.py art.txt --braille ascii --mode gradient -o art_ansi.txt

# Палитры: cyan, rainbow, vaporwave, fire, ocean
python tools/ascii_to_ansi.py art.txt --braille ascii --palette rainbow -o art_ansi.txt
```

## Режимы

- **gradient** — градиент по строкам (по умолчанию)
- **char** — градиент по символам слева направо
- **char-based** — цвет по плотности символа (#@ ярче, . тусклее)
- **palette** — цикл по палитре для каждого символа
- **line** — каждая строка в своём цвете

Приложение использует `art_ansi.txt` в шапке, если файл существует.
