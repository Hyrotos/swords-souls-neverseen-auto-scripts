# Swords & Souls Neverseen — Auto Training Scripts

[English version](README_EN.md)

Набор из пяти Python-скриптов для автоматизации тренировок в
**Swords & Souls Neverseen** на Windows:

| Тренировка | Папка | Запуск |
| --- | --- | --- |
| Меч | [`sword`](sword) | `sword/START_FIXED.bat` |
| Лук | [`archery`](archery) | `archery/START.bat` |
| Магия | [`magic`](magic) | `magic/START.bat` |
| Защита | [`shield`](shield) | `shield/START.bat` |
| Ловкость | [`agility`](agility) | `agility/START.bat` |

Скрипты читают состояние запущенной игры через Frida и отправляют обычные
клавиатурные или мышиные команды. Файлы игры не изменяются.

## Видео-демонстрация

[▶ Посмотреть или скачать видео (MP4, 98,34 МБ)](https://github.com/ibadullaevrahil67-maker/swords-souls-neverseen-auto-scripts/releases/download/v1.0.0/swords-souls-auto-scripts-demo.mp4)

В видео показаны установка библиотек командой из инструкции и работа всех пяти
скриптов.

## Требования

- Windows 10 или Windows 11 (64-bit);
- Steam-версия Swords & Souls Neverseen;
- 64-битный Python 3.11 или 3.12;
- игра и скрипт должны быть запущены от одного пользователя Windows.

## Установка Python

Скачайте 64-битный Python с [python.org](https://www.python.org/downloads/windows/)
и при установке отметьте **Add Python to PATH**.

Альтернативный вариант через Windows Package Manager:

```powershell
winget install -e --id Python.Python.3.12
```

После установки откройте новое окно PowerShell или командной строки и
проверьте:

```powershell
python --version
```

## Установка библиотек

Откройте PowerShell или командную строку в любом месте и выполните:

```powershell
python -m pip install "frida>=17.2,<18" "mss>=10.0" "numpy>=2.0" "opencv-python>=4.10"
```

Если терминал открыт в корневой папке проекта, зависимости также можно
установить из файла:

```powershell
python -m pip install -r requirements.txt
```

## Запуск

1. Запустите игру и откройте нужную тренировку.
2. Откройте папку нужной тренировки и запустите находящийся там BAT-файл.
3. Вернитесь в окно игры.

Управление во всех режимах:

- `F8` — пауза или продолжение автоматизации;
- `F9` — остановка скрипта.

Подробности о каждой автоматизации находятся в `README_RU.md` внутри её папки.

## Важно

- Скрипты рассчитаны на текущую структуру Unity/Mono-версии игры. После
  обновления игры адреса и внутренние классы могут измениться.
- Используйте на свой риск и учитывайте правила игры и Steam.
