# Swords & Souls Neverseen — Auto Training Scripts

[English version](README_EN.md)

Набор из пяти Python-скриптов для автоматизации тренировок в
**Swords & Souls Neverseen**. Все режимы работают на Windows, а также на Linux,
если игра запущена через Proton в сеансе X11/XWayland:

| Тренировка | Папка | Запуск |
| --- | --- | --- |
| Меч | [`sword`](sword) | Windows: `START_FIXED.bat`; Linux/Proton: `START_LINUX.sh` |
| Лук | [`archery`](archery) | Windows: `START.bat`; Linux/Proton: `START_LINUX.sh` |
| Магия | [`magic`](magic) | Windows: `START.bat`; Linux/Proton: `START_LINUX.sh` |
| Защита | [`shield`](shield) | Windows: `START.bat`; Linux/Proton: `START_LINUX.sh` |
| Ловкость | [`agility`](agility) | Windows: `START.bat`; Linux/Proton: `START_LINUX.sh` |

Скрипты читают состояние запущенной игры через Frida и отправляют обычные
клавиатурные или мышиные команды. Файлы игры не изменяются.

## Видео-демонстрация

[▶ Посмотреть или скачать видео (MP4, 98,34 МБ)](https://github.com/ibadullaevrahil67-maker/swords-souls-neverseen-auto-scripts/releases/download/v1.0.0/swords-souls-auto-scripts-demo.mp4)

В видео показаны установка библиотек командой из инструкции и работа всех пяти
скриптов.

## Требования

- Windows 10/11 (64-bit) либо Linux + Steam/Proton;
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

Откройте PowerShell или командную строку в корне репозитория и выполните:

```powershell
python -m pip install -r requirements.txt
```

На Linux установите зависимости в виртуальное окружение
проекта; Linux-зависимость X11 выберется автоматически.

## Запуск

В Windows запустите игру, откройте нужную тренировку и запустите BAT-файл
соответствующего режима. В Linux запустите игру через Proton в
X11/XWayland и выполните из корня репозитория файл запуска нужного режима,
например `bash shield/START_LINUX.sh`.

Чтобы запустить все пять помощников сразу, используйте `START_ALL.bat` в
Windows или `bash START_ALL_LINUX.sh` в Linux. Каждый помощник реагирует только
на свою тренировку, поэтому автоматически играет тот, чья тренировка открыта.
Не запускайте вместо этого несколько отдельных файлов запуска одновременно: в
Linux помощники в разных процессах могут обрушить игру при завершении.

Управление во всех режимах:

- `F8` — пауза или продолжение автоматизации;
- `F9` — остановка скрипта.

Подробности о каждой автоматизации находятся в `README_RU.md` внутри её папки.

## Важно

- Скрипты рассчитаны на текущую структуру Unity/Mono-версии игры. После
  обновления игры адреса и внутренние классы могут измениться.
- Используйте на свой риск и учитывайте правила игры и Steam.
