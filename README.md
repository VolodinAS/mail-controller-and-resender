### Скрипт проверки почты

Нужно забросить в `crontab -e`:

```bash
0 12 * * * cd /root/projects/bots/mail-controller-and-resender && /root/projects/bots/mail-controller-and-resender/venv/bin/python _main_.py >> /var/log/mail-bot.log 2>&1
```