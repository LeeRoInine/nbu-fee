# NBU news feed

The National Bank of Ukraine (bank.gov.ua) publishes no RSS feed. A GitHub Actions job runs
`nbu_feed.py` every hour, reads the bank's news list and two research-event categories, and commits RSS feeds with the
full text of each item (event pages are re-read for two weeks after the event, as slides and video get added):

| Feed | URL to paste into NetNewsWire (⌘N) |
|---|---|
| Ukrainian news | https://raw.githubusercontent.com/LeeRoInine/nbu-fee/main/docs/feed.xml |
| English news | https://raw.githubusercontent.com/LeeRoInine/nbu-fee/main/docs/feed-en.xml |
| Open research seminars (Відкриті дослідницькі семінари) | https://raw.githubusercontent.com/LeeRoInine/nbu-fee/main/docs/feed-seminars.xml |
| Practical workshops (Практичні воркшопи) | https://raw.githubusercontent.com/LeeRoInine/nbu-fee/main/docs/feed-workshops.xml |

- **Run now:** Actions tab → *Update NBU feed* → *Run workflow*.
- **If it fails:** GitHub emails you; the last good feed stays in place.
- **If GitHub pauses the schedule** (it does that to repos with no activity for 60 days):
  Actions tab → *Update NBU feed* → *Enable workflow*.
