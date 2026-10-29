# Follow Check

A local dashboard for your Instagram followers. Drop in your data export and see who doesn't follow you back, who you don't follow back, who unfollowed you between exports, and a few other stats.

Everything stays on your machine. It needs only Python 3.9+ and no packages.

## Usage

1. **Export your data from Instagram.** Go to Accounts Center → *Your information and permissions* → *Download your information* → *Download or transfer information* → *Some of your information* → **Followers and following**. Pick *Download to device*, format **JSON** (HTML also works), date range **All time**.
2. **Put the `.zip` in [`exports/`](exports/).** You can drop it in as-is or extract it first.
3. **Run the dashboard:**

   ```
   python server.py
   ```

   This imports any new exports and opens http://localhost:8000.

While the server is running it watches `exports/`. When a new export finishes landing there it's imported automatically and the open dashboard refreshes itself, so there's nothing to click. Tip: set your browser to save the Instagram download straight into `exports/`. (The **Rescan exports** button is still there if you want it.)

Once an export is saved in the database it's **deleted from `exports/`**, so the folder stays empty. Exports that can't be imported are left there. Pass `--keep-exports` to `server.py` or `ingest.py` if you'd rather keep them. Since `instagram.db` is then the only copy of your history, back it up now and then.

Next time, just download a fresh export into `exports/`. Each export is saved as a snapshot, so the dashboard can show who followed or unfollowed you since the previous one and chart your counts over time.

## What you get

- Followers, following, mutuals and follow-back rate, with the change since your last export
- **Not following you back**: sorted by when you followed them. Use *Ignore* for accounts you don't expect a follow-back from (celebrities, brands) to hide them from the count.
- **You don't follow back**, **Mutuals**, and pending follow requests / recently unfollowed if they're in the export
- **Since your last export**: new followers, who unfollowed you (red if you still follow them), and who you followed or unfollowed
- Monthly follow activity chart, history across exports, and a few fun facts

## Files

| | |
|---|---|
| `ingest.py` | Reads exports into `instagram.db` (SQLite). `python ingest.py` runs it on its own. |
| `report.py` | Works out the stats and lists from the database |
| `server.py` | Local web server and JSON API (`--port`, `--no-browser`) |
| `web/` | The dashboard page |

To start over, delete `instagram.db`.

## License

[MIT](LICENSE)
