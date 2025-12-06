# Dilato-Crawling

A Playwright-based Python tool for automatically extracting **Template Library** data from the **Dilato** platform (web.dilato.app).
The script logs in, navigates all template categories, opens each template, and saves structured content for analysis.

---

## 🚀 Features

* Automatic login to Dilato
* Crawls all template categories & items
* Extracts:

  * Template names & IDs
  * API payloads
  * DOM paragraph lines
  * Dropdown / radio options
  * All visible text from the editor
* Saves results to:

  * `dilato_library_raw.json`
  * `dilato_all_strings.txt`

---

## 📂 Files

| File                              | Description                            |
| --------------------------------- | -------------------------------------- |
| **dilato_library_scrap_final.py** | Main Playwright crawler script         |
| **dilato_library_raw.json**       | Full structured extraction (API + DOM) |
| **dilato_all_strings.txt**        | All unique cleaned strings             |
| **README.md**                     | Project documentation                  |

---

## ▶️ Usage

### Install:

```bash
pip install playwright
playwright install
```

### Run:

```bash
python dilato_library_scrap_final.py
```

Edit inside the script if you want headless mode:

```python
run(headless=True)
```

---

## ⚠️ Notes

* Requires valid Dilato login credentials.
* Designed for internal use (Saman Salamat).
* Changes to the Dilato UI may require script updates.
