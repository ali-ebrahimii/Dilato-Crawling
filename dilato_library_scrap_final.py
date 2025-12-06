import os, re, json, time
from collections import defaultdict, deque
from pathlib import Path
from playwright.sync_api import sync_playwright


EMAIL    = "ee.aliebrahimi@gmail.com"
PASSWORD = "#Aa48506975"


WEB_BASE   = "https://web.dilato.app"
LOGIN_HASH = "#/login"
DASH_HASH  = "#/dashboard"
API_HOST   = "api.dilato.app"

OUT_RAW     = "dilato_library_raw.json"
OUT_STRINGS = "dilato_all_strings.txt"
OUT_DIR = Path(".") 
OUT_DIR.mkdir(exist_ok=True)


def _dedup(seq):
    seen=set() 
    out=[]
    for x in seq:
        if isinstance(x,str):
            x=x.strip()
        if not x:
            continue
        if x not in seen:
            seen.add(x) 
            out.append(x)
    return out

def _norm(s):
    return re.sub(r"\s+"," ", (s or "").strip()).casefold()

def ensure_logged_in(page):
    page.goto(f"{WEB_BASE}/{LOGIN_HASH}", wait_until="domcontentloaded")
    email=(page.locator('input[placeholder="Email"]').or_(page.locator('input[type="email"]'))).first
    pw=(page.locator('input[placeholder="Password"]').or_(page.locator('input[type="password"]'))).first
    email.fill(EMAIL, timeout=25000); pw.fill(PASSWORD, timeout=25000)
    page.get_by_role("button", name=re.compile(r"^Log ?in$", re.I)).first.click(timeout=25000)
    page.wait_for_url(re.compile(r"#/(dashboard|home)"), timeout=60000)
    page.wait_for_load_state("networkidle")

def click_templates_tab(page):
    page.locator("[data-cy='tab-switcher-templates-button']").click(timeout=8000)
    page.wait_for_timeout(250)

def find_library_root(page):
    sidebar = page.locator("[data-cy='sidebar-side-container']").first
    lib_header = sidebar.locator('div[title="Library"]').first
    root_li = lib_header.locator("xpath=ancestor::li[@role='treeitem']").first
    group_ul = root_li.locator("ul[role='group']").first
    return sidebar, root_li, group_ul

def list_categories(group_ul):
    items = group_ul.locator("> li[role='treeitem']")
    results=[]
    cnt = items.count()
    for i in range(cnt):
        li = items.nth(i)
        inp = li.locator('input[data-cy="folder-cell-name"]').first
        if not inp.count():
            continue
        try:
            name = inp.input_value(timeout=1500)
        except:
            name = inp.inner_text(timeout=1500)
        results.append((li, (name or "").strip()))
    return results

def ensure_expanded(li):
    try:
        if li.get_attribute("aria-expanded") == "true":
            return
        row = li.locator('div[data-cy^="folder-cell-library|"]').first
        (row if row.count() else li).click(timeout=2000)
        time.sleep(0.15)
    except:
        pass

def category_group(li):
    return li.locator("ul[role='group']").first

def template_rows(li):
    grp = category_group(li)
    return grp.locator('div[data-cy^="template-cell-"]:has([data-cy="template-cell-name"])')

def extract_row_name(row):
    name_el = row.locator('[data-cy="template-cell-name"]').first
    if not name_el.count():
        return None
    try:
        return (name_el.input_value(timeout=1200) or name_el.inner_text(timeout=1200)).strip()
    except:
        try:
            return name_el.inner_text(timeout=1200).strip()
        except:
            return None

def scroll_list(container, px=1200):
    """Return (prev_top, new_top, scroll_height, client_height) to detect bottom."""
    try:
        prev_top = container.evaluate("el=>el.scrollTop")
        scroll_h = container.evaluate("el=>el.scrollHeight")
        client_h = container.evaluate("el=>el.clientHeight")
        container.evaluate("(el,dy)=>{el.scrollTop = el.scrollTop + dy}", px)
        container.page.wait_for_timeout(150)
        new_top = container.evaluate("el=>el.scrollTop")
        return prev_top, new_top, scroll_h, client_h
    except:
        container.page.wait_for_timeout(150)
        return None, None, None, None


NOISE_RE = re.compile(
    r"^(Home|Actions|Copy( this template)?|Close (Preview|sidebar)|Upgrade now|Use AI commands|Open Quick Tips|"
    r"Note copied!|Try demo template|Visit our Help Center|New note|New template|Search( for a template)?)$",
    re.I
)
def _editor(page):
    return page.locator('[data-cy="scroll-container"]').first

def collect_visible_texts(page):
    deep = page.evaluate(r"""
    () => {
      function deep(el){
        let out=[];
        if (!el) return out;
        if (el.nodeType===Node.TEXT_NODE){
          const t = (el.textContent||'')
                      .replace(/\u200d|\uFEFF/g,' ')
                      .replace(/\s+/g,' ')
                      .trim();
          if (t) out.push(t);
        }
        if (el.shadowRoot){ el.shadowRoot.childNodes.forEach(n=> out=out.concat(deep(n))); }
        el.childNodes.forEach(n=> out=out.concat(deep(n)));
        return out;
      }
      const root = document.querySelector('[data-cy="scroll-container"]');
      return deep(root);
    }
    """) or []
    cleaned = [t.strip() for t in deep if len(t.strip()) >= 1 and not NOISE_RE.match(t)]
    return _dedup(cleaned)

def _line_probe(el_handle):
    """
    JS executed inside a single line element to keep order and split 'label' from controls.
    Returns:
      {
        label: str|None,
        plain_text: "...",          // all human text on the line
        inline_values: [...],       // blue inline dropdown current values
        radio_buttons: [...],       // radio labels visible on this line
        placeholders: int           // number of placeholders on this line
      }
    """
    return el_handle.evaluate(r"""
    (el) => {
      const CONTROL_SEL = '[data-cy="editor-radio-element"],[data-cy="editor-replaced-element"],[data-cy="editor-placeholder-element-input"]';
      const TEXT_SEL    = '[data-cy="editor-text-node"]';

      function getText(node){
        const s = (node.textContent || "")
          .replace(/\u200d|\uFEFF/g,' ')
          .replace(/\s+/g,' ')
          .trim();
        return s;
      }

      // gather plain text in order, and detect first control boundary
      let prefixParts=[];
      let plainParts=[];
      let seenControl=false;

      // Walk only shallow children to preserve order boundaries
      for (const n of el.childNodes){
        if (n.nodeType === Node.TEXT_NODE){
          const t = getText(n);
          if (t){
            plainParts.push(t);
            if (!seenControl) prefixParts.push(t);
          }
          continue;
        }
        if (!(n instanceof Element)) continue;

        if (n.matches(CONTROL_SEL) || n.querySelector(CONTROL_SEL)){
          seenControl = true;
        }

        // Add any visible text from text nodes/leaves contained
        const txtNodes = n.querySelectorAll(TEXT_SEL);
        if (txtNodes.length){
          for (const tn of txtNodes){
            const t = getText(tn);
            if (t){
              plainParts.push(t);
              if (!seenControl) prefixParts.push(t);
            }
          }
        }else{
          const t = getText(n);
          if (t){
            plainParts.push(t);
            if (!seenControl) prefixParts.push(t);
          }
        }
      }

      // radio labels on this line
      const radioBtns = Array.from(el.querySelectorAll('[data-cy^="editor-radio-element-option-"] button'))
        .map(b=> (b.textContent||"").replace(/\s+/g,' ').trim())
        .filter(Boolean);

      // inline replaced tokens current text
      const inlineVals = Array.from(el.querySelectorAll('[data-cy="editor-replaced-element"]'))
        .map(v => (v.textContent||"").replace(/\s+/g,' ').trim())
        .filter(Boolean);

      // placeholder count
      const placeholders = el.querySelectorAll('[data-cy="editor-placeholder-element-input"]').length;

      // compute label (text strictly before first control)
      let label = prefixParts.join(' ').trim();
      if (label.endsWith(':')) label = label.slice(0,-1).trim();
      if (!label) label = null;

      return {
        label,
        plain_text: plainParts.join(' ').trim(),
        inline_values: Array.from(new Set(inlineVals)),
        radio_buttons: Array.from(new Set(radioBtns)),
        placeholders
      };
    }
    """)

def _click_line_triggers_and_collect(page, line_el):
    """
    Clicks only the triggers inside this line and collects the currently opened popover/listbox text.
    """
    grabbed=[]
    triggers = line_el.locator(
        "[data-cy='element-dropdown-button'], "
        "[data-cy='editor-radio-element-dropdown-chevron'], "
        "[data-cy='element-dropdown-root'] button[role='combobox']"
    )
    m = triggers.count()
    for i in range(m):
        btn = triggers.nth(i)
        try:
            btn.scroll_into_view_if_needed(timeout=1000)
            btn.click(timeout=1500)
            page.wait_for_timeout(180)
            # Collect open popper/listbox content (global because popper is appended to body)
            texts=[]
            try:
                listboxes = page.locator('[role="listbox"]')
                if listboxes.count():
                    lb = listboxes.last
                    items = (lb.locator('[role="option"]').all_inner_texts() or
                             lb.locator('button').all_inner_texts() or [])
                    for t in items:
                        t=(t or "").strip()
                        if t: texts.append(t)
                wrappers = page.locator('[data-radix-popper-content-wrapper], [data-state="open"]')
                if wrappers.count():
                    w = wrappers.last
                    items = w.locator('button, [role="option"], [data-cy="menu-item"]').all_inner_texts()
                    for t in (items or []):
                        t=(t or "").strip()
                        if t and t not in texts:
                            texts.append(t)
            except:
                pass
            grabbed += texts
        except:
            pass
        finally:
            try: page.keyboard.press("Escape")
            except: pass
            page.wait_for_timeout(60)
    return _dedup(grabbed)

def scrape_editor_grouped(page):
    """
    Return (dom_lines, dom_texts_flat).
    - dom_lines: [{label, plain_text, inline_values, radio_buttons, dropdown_options, placeholders}, ...]
    - dom_texts_flat: flattened unique strings across the whole editor
    """
    ed = _editor(page)
    if not ed.count():
        return [], []

    try:
        ed.evaluate("el=>{el.scrollTop=0}")
        page.wait_for_timeout(100)
    except:
        pass

    lines = page.locator('[data-cy="editor-paragraph-element"]')
    total = lines.count()

    dom_lines=[]
    all_strings=[]

    max_passes = 30
    seen_indices=set()
    last_seen_total=-1
    stagnant=0

    for _ in range(max_passes):
        cnt_now = lines.count()
        for i in range(cnt_now):
            if i in seen_indices:
                continue
            line_el = lines.nth(i)
            try:
                line_el.scroll_into_view_if_needed(timeout=1200)
            except:
                pass
            page.wait_for_timeout(40)

            try:
                info = _line_probe(line_el)
            except:
                continue

            try:
                dropdown_opts = _click_line_triggers_and_collect(page, line_el)
            except:
                dropdown_opts = []

            block = {
                "label": info.get("label"),
                "plain_text": info.get("plain_text"),
                "inline_values": info.get("inline_values", []),
                "radio_buttons": info.get("radio_buttons", []),
                "dropdown_options": dropdown_opts,
                "placeholders": info.get("placeholders", 0),
            }
            dom_lines.append(block)

            all_strings += info.get("inline_values", [])
            all_strings += info.get("radio_buttons", [])
            if info.get("plain_text"):
                all_strings.append(info["plain_text"])
            all_strings += dropdown_opts
            if info.get("placeholders", 0):
                all_strings += ["[placeholder]"] * info["placeholders"]

            seen_indices.add(i)

        stagnant = stagnant + 1 if cnt_now == last_seen_total else 0
        last_seen_total = cnt_now
        if stagnant >= 2:
            break

        try:
            prev_top = ed.evaluate("el=>el.scrollTop")
            ed.evaluate("el=>{el.scrollTop = el.scrollTop + 800}")
            page.wait_for_timeout(140)
            new_top = ed.evaluate("el=>el.scrollTop")
            if new_top == prev_top:
                break
        except:
            break

    return dom_lines, _dedup(all_strings)



def run(headless=False):
    raw_by_cat=defaultdict(list)
    all_strings=[]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()

        recent_payloads = deque(maxlen=120)

        def on_api(resp):
            if API_HOST not in resp.url:
                return
            ctype = (resp.headers or {}).get("content-type","")
            if "application/json" not in ctype:
                return
            try:
                data = resp.json()
            except:
                return
            d = data.get("data") if isinstance(data, dict) else None
            if not isinstance(d, dict):
                return
            ts = time.time()

            if "id" in d and isinstance(d.get("content"), list):
                recent_payloads.append({"ts": ts, "payload": d}); return
            if "template" in d and isinstance(d["template"], dict) and "content" in d["template"]:
                recent_payloads.append({"ts": ts, "payload": d["template"]}); return

        page.on("response", on_api)

        ensure_logged_in(page)
        page.goto(f"{WEB_BASE}/{DASH_HASH}", wait_until="networkidle")
        click_templates_tab(page)

        sidebar, root_li, group_ul = find_library_root(page)
        if not (sidebar and root_li and group_ul):
            raise RuntimeError("Could not locate Library tree")

        categories = list_categories(group_ul)
        for li, cat in categories:
            print(f"[+] Category: {cat}")
            ensure_expanded(li)
            grp = category_group(li)

            seen_row_names=set()
            saved_template_ids=set()

            last_seen_total=-1
            stagnant_rounds=0
            max_scrolls = 80
            scrolls_done = 0
            bottom_hits = 0

            while True:
                rows = template_rows(li)
                count_now = rows.count()
                new_saved_this_pass = 0

                for i in range(count_now):
                    row = rows.nth(i)
                    row_name = extract_row_name(row) or f"row#{i}"
                    if row_name in seen_row_names:
                        continue
                    seen_row_names.add(row_name)

                    try: row.scroll_into_view_if_needed(timeout=1500)
                    except: pass
                    try: row.click(timeout=2400)
                    except:
                        box = row.bounding_box() or {}
                        if box:
                            page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)

                    try: page.wait_for_selector("[data-cy='scroll-container']", timeout=6000)
                    except: pass
                    page.wait_for_timeout(700)

                    try:
                        editor_name_el = page.locator('[data-cy="editor-name-field"]').first
                        current_editor_name = (editor_name_el.input_value(timeout=1400) or
                                               editor_name_el.inner_text(timeout=1400) or "").strip()
                    except:
                        current_editor_name = row_name

                    click_ts = time.time()
                    chosen = None
                    deadline = click_ts + 4.0
                    while time.time() < deadline and not chosen:
                        for item in list(recent_payloads)[::-1]:
                            p = item["payload"]
                            if item["ts"] < click_ts - 4.0:
                                continue
                            pname = (p.get("name") or "").strip()
                            if _norm(pname) in (_norm(row_name), _norm(current_editor_name)):
                                chosen = p; break
                        if not chosen:
                            page.wait_for_timeout(120)
                    if not chosen and recent_payloads:
                        chosen = list(recent_payloads)[-1]["payload"]

                    dom_lines, flat_for_this_template = scrape_editor_grouped(page)
                    all_strings += flat_for_this_template

                    if chosen:
                        tid = chosen.get("id")
                        tname = (chosen.get("name") or current_editor_name or row_name).strip()
                        if tid and tid in saved_template_ids:
                            continue
                        if tid:
                            saved_template_ids.add(tid)
                        raw_by_cat[cat].append({
                            "category": cat,
                            "template_id": tid,
                            "template_name": tname,
                            "api_payload": chosen,
                            "dom_lines": dom_lines,
                            "dom_texts_flat": flat_for_this_template
                        })
                        new_saved_this_pass += 1
                        print(f"    • saved: {tname}")
                    else:
                        if flat_for_this_template and (len(flat_for_this_template) >= 8):
                            raw_by_cat[cat].append({
                                "category": cat,
                                "template_id": None,
                                "template_name": current_editor_name or row_name,
                                "api_payload": None,
                                "dom_lines": dom_lines,
                                "dom_texts_flat": flat_for_this_template
                            })
                            new_saved_this_pass += 1
                            print(f"    • saved (DOM-only): {current_editor_name or row_name}")

                stagnant_rounds = (stagnant_rounds + 1) if new_saved_this_pass == 0 else 0
                prev_top, new_top, scroll_h, client_h = scroll_list(grp, px=1400)
                scrolls_done += 1

                at_bottom = False
                try:
                    if prev_top is not None and new_top is not None and scroll_h and client_h:
                        at_bottom = (new_top == prev_top) or (new_top + client_h >= scroll_h - 2)
                except:
                    pass
                bottom_hits = bottom_hits + 1 if at_bottom else 0

                if stagnant_rounds >= 2 or bottom_hits >= 2 or scrolls_done >= max_scrolls:
                    break

            print(f"    ✓ finished {cat} ({len(raw_by_cat[cat])} items)")

        page.context.browser.close()


    with open(OUT_RAW,"w",encoding="utf-8") as f:
        json.dump(raw_by_cat,f,indent=2,ensure_ascii=False)

    uniq = sorted(set(s.strip() for s in all_strings if s and s.strip()), key=str.lower)
    with open(OUT_STRINGS,"w",encoding="utf-8") as f:
        for s in uniq:
            f.write(s+"\n")

    print("\nSaved:")
    print(f"- {OUT_RAW}\n- {OUT_STRINGS}\n")

if __name__=="__main__":
    run(headless=False)
