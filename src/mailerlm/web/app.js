"use strict";
// mailerlm web app: plain JavaScript, no build step. Every read and write goes through POST /api/mailerlm.

const app = document.getElementById("app");

function h(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (key === "class") node.className = value;
    else if (key === "on") for (const [event, fn] of Object.entries(value)) node.addEventListener(event, fn);
    else if (value === true) node.setAttribute(key, "");
    else if (value !== false && value != null) node.setAttribute(key, value);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

async function api(method, params) {
  const response = await fetch("/api/mailerlm", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Mailerlm": "1" },
    body: JSON.stringify({ method, params: params || {} }),
  });
  const body = await response.json().catch(() => ({ error: "the server sent something unreadable" }));
  if (!response.ok) throw new Error(body.error || `error ${response.status}`);
  return body.result;
}

function toast(message, bad) {
  const box = document.getElementById("toast");
  const item = h("div", { class: bad ? "bad" : "" }, message);
  box.append(item);
  setTimeout(() => item.remove(), bad ? 7000 : 2600);
}

async function attempt(work, success) {
  try {
    const result = await work();
    if (success) toast(success);
    return result;
  } catch (error) {
    toast(error.message, true);
    return undefined;
  }
}

const enc = encodeURIComponent;
const when = (iso) => (iso || "").replace("T", " ").slice(0, 16);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
function statusChip(status) { return h("span", { class: `chip ${status}` }, status); }

// An "Edit" toggle: shows `view`, and on Edit swaps in `form(done)`; done(true) re-renders after a save.
function editable(title, view, form, onSaved, extra) {
  const body = h("div");
  const edit = h("button", { class: "quiet" }, "Edit");
  const panel = h("section", { class: "panel" }, h("header", {}, h("h2", {}, title), h("div", { class: "row" }, extra, edit)), body);
  const show = () => { body.replaceChildren(view); edit.hidden = false; };
  edit.addEventListener("click", () => {
    edit.hidden = true;
    body.replaceChildren(form((saved) => { if (saved) onSaved(); else show(); }));
  });
  show();
  return panel;
}

function formButtons(save, cancel, label) {
  return h("div", { class: "row", style: "margin-top:12px" },
    h("button", { class: "primary", on: { click: save } }, label || "Save"),
    h("button", { on: { click: cancel } }, "Cancel"));
}

function inlineConfirm(label, onConfirm, what) {
  const slot = h("span");
  const ask = h("button", { class: "danger", on: { click: () => {
    slot.replaceChildren(
      h("span", { class: "small" }, `${what || "Delete"}? `),
      h("button", { class: "danger", on: { click: onConfirm } }, "Yes"),
      h("button", { on: { click: () => slot.replaceChildren(ask) } }, "No"));
  } } }, label);
  slot.append(ask);
  return slot;
}

function field(label, input) { return h("div", {}, h("label", {}, label), input); }
function text(value, props) { return h("input", { type: "text", value: value || "", ...props }); }

// ---- routes ------------------------------------------------------------------------------

const routes = [
  [/^#\/?$/, "", home],
  [/^#\/campaigns$/, "campaigns", campaignsPage],
  [/^#\/campaigns\/(.+)$/, "campaigns", campaignPage],
  [/^#\/lists$/, "lists", listsPage],
  [/^#\/lists\/(.+)$/, "lists", listPage],
  [/^#\/contacts$/, "contacts", contactsPage],
  [/^#\/templates$/, "templates", templatesPage],
  [/^#\/templates\/(.+)$/, "templates", templatePage],
  [/^#\/settings$/, "settings", settingsPage],
];

async function render() {
  const hash = location.hash || "#/";
  for (const link of document.querySelectorAll("nav a")) link.classList.remove("on");
  for (const [pattern, nav, view] of routes) {
    const match = hash.match(pattern);
    if (!match) continue;
    const active = document.querySelector(`nav a[data-nav="${nav}"]`);
    if (active) active.classList.add("on");
    app.replaceChildren(h("p", { class: "small" }, "Loading…"));
    try {
      const page = await view(...match.slice(1).map(decodeURIComponent));
      app.replaceChildren(page);
    } catch (error) {
      app.replaceChildren(h("div", { class: "notice bad" }, error.message), h("a", { href: "#/" }, "Back to home"));
    }
    window.scrollTo(0, 0);
    return;
  }
  app.replaceChildren(h("div", { class: "notice bad" }, "That page does not exist."), h("a", { href: "#/" }, "Back to home"));
}
window.addEventListener("hashchange", render);
const refresh = () => render();

// ---- home --------------------------------------------------------------------------------

async function home() {
  const data = await api("overview");
  const members = data.lists.reduce((sum, list) => sum + list.members, 0);
  const waiting = data.lists.reduce((sum, list) => sum + (list.by_status.imported || 0), 0);
  const subscribed = data.lists.reduce((sum, list) => sum + (list.by_status.subscribed || 0), 0);
  return h("div", {},
    h("h1", {}, "Your mailing lists"),
    h("p", { class: "sub" }, "Write a campaign, choose who it goes to, and check it before it is sent."),
    h("div", { class: "row", style: "margin-bottom:16px" },
      h("a", { class: "button primary", href: "#/campaigns" }, "Write or edit a campaign")),
    h("div", { class: "grid" },
      card("#/campaigns", plural(data.campaigns.length, "campaign"),
        data.campaigns.length ? `Latest: ${data.campaigns[0].name}` : "Create your first from a starter"),
      card("#/lists", plural(data.lists.length, "list"), `${plural(members, "membership")} · ${subscribed} subscribed · ${waiting} waiting to confirm`),
      card("#/contacts", plural(data.contacts, "contact"), "Add, import, tag and edit people"),
      card("#/templates", plural(data.templates, "template"), "The frame your letters sit in"),
      card("#/settings", data.sender_set ? "Sender and address set" : "Sender not set yet",
        data.sender_set ? "Defaults for every campaign" : "Needed before anything can be sent", !data.sender_set)));
}

function card(href, big, small, warn) {
  return h("a", { class: `card${warn ? " warn" : ""}`, href }, h("div", { class: "big" }, big), h("div", { class: "small" }, small));
}

// ---- settings ----------------------------------------------------------------------------

const SENDER_FIELDS = [
  ["sender_name", "Sender name", "Shown as the sender and signed in the footer"],
  ["sender_address", "Sender address", "An address on your verified sending domain"],
  ["reply_to", "Reply-to address", "Where replies, confirmations and unsubscribes arrive"],
  ["postal_address", "Postal address (footer)", "Required in every commercial email"],
];

async function settingsPage() {
  const { settings } = await api("settings_get");
  const view = h("div", {}, SENDER_FIELDS.map(([key, label]) =>
    h("p", {}, h("span", { class: "small" }, label + ": "), settings[key] || h("em", { class: "small" }, "not set"))));
  const form = (done) => {
    const inputs = Object.fromEntries(SENDER_FIELDS.map(([key]) => [key, text(settings[key])]));
    return h("div", {}, SENDER_FIELDS.map(([key, label, hint]) => field(`${label} — ${hint}`, inputs[key])),
      formButtons(async () => {
        const values = Object.fromEntries(Object.entries(inputs).map(([k, input]) => [k, input.value]));
        if (await attempt(() => api("settings_save", values), "Saved")) done(true);
      }, () => done(false)));
  };
  return h("div", {}, h("h1", {}, "Settings"),
    h("p", { class: "sub" }, "Defaults for every campaign. A campaign can override any of them on its own page."),
    editable("Sender and footer", view, form, refresh));
}

// ---- lists and contacts ------------------------------------------------------------------

async function listsPage() {
  const { lists } = await api("lists_show");
  const name = text("", { placeholder: "e.g. book-interest" });
  const add = async () => {
    if (await attempt(() => api("lists_add", { name: name.value }), "List created")) { location.hash = `#/lists/${enc(name.value.trim())}`; }
  };
  return h("div", {},
    h("h1", {}, "Lists"),
    h("p", { class: "sub" }, "Each list is an audience. People join as “imported” and are only mailed after they confirm."),
    h("section", { class: "panel" }, h("h2", {}, "New list"),
      field("Name", name), h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "primary", on: { click: add } }, "Create list"))),
    lists.length ? h("div", { class: "grid" }, lists.map((list) => card(`#/lists/${enc(list.name)}`, list.name,
      `${plural(list.members, "member")} · ` + (Object.entries(list.by_status).map(([s, n]) => `${n} ${s}`).join(", ") || "empty")))) : h("p", { class: "empty" }, "No lists yet."));
}

async function listPage(name) {
  const { lists } = await api("lists_show");
  const list = lists.find((item) => item.name === name);
  if (!list) throw new Error(`No list named “${name}”.`);
  const people = await api("contacts_find", { list_name: name, show_full_addresses: true });
  const allLists = lists.map((item) => item.name);
  const view = h("div", {},
    h("p", {}, h("span", { class: "small" }, "Members: "), `${list.members} (${Object.entries(list.by_status).map(([s, n]) => `${n} ${s}`).join(", ") || "none"})`),
    h("p", {}, h("span", { class: "small" }, "Sender override: "), list.sender_override ? `${list.sender_name} ${list.sender_address}` : "none (uses Settings)"));
  const form = (done) => {
    const newName = text(list.name), senderName = text(list.sender_name), senderAddress = text(list.sender_address);
    return h("div", {}, field("Name", newName), field("Sender name (optional override)", senderName), field("Sender address (optional override)", senderAddress),
      formButtons(async () => {
        if (await attempt(() => api("lists_edit", { name: list.name, new_name: newName.value, sender_name: senderName.value, sender_address: senderAddress.value }), "Saved")) {
          location.hash = `#/lists/${enc(newName.value.trim())}`;
        }
      }, () => done(false)),
      h("div", { style: "margin-top:12px" }, inlineConfirm("Delete this list", async () => {
        if (await attempt(() => api("lists_delete", { name: list.name }), "List deleted")) location.hash = "#/lists";
      }, "Delete the list (people stay on your other lists)")));
  };
  return h("div", {}, h("p", {}, h("a", { href: "#/lists" }, "← All lists")), h("h1", {}, list.name),
    editable("List details", view, form, refresh),
    importPanel(name),
    h("section", { class: "panel" }, h("header", {}, h("h2", {}, `People on this list (${people.count})`)),
      people.contacts.length ? people.contacts.map((person) => personRow(person, allLists, name)) : h("p", { class: "empty" }, "Nobody yet. Paste some people above.")));
}

function importPanel(listName) {
  const box = h("textarea", { class: "short", placeholder: "One per line:\nAda Example: ada@example.com\nGrace <grace@example.com>\nhttps://www.linkedin.com/in/someone" });
  const tag = text("", { placeholder: "optional tag, e.g. early-access" });
  const result = h("div");
  const run = async (dry) => {
    const tags = tag.value.trim() ? [tag.value.trim()] : [];
    const board = await attempt(() => api("contacts_import_text", { text: box.value, list_name: listName || null, tags, dry_run: dry }));
    if (!board) return;
    result.replaceChildren(h("div", { class: `notice ${board.rejected_line_numbers.length ? "warn" : "good"}` },
      `${dry ? "Would add" : "Added"} ${board.added} new, ${board.already_present} already known, ${board.memberships_added} joined the list, ${board.tags_added} tags.` +
      (board.rejected_line_numbers.length ? ` Lines with no email or LinkedIn address: ${board.rejected_line_numbers.join(", ")}.` : "")));
    if (!dry) { box.value = ""; setTimeout(refresh, 900); }
  };
  return h("section", { class: "panel" }, h("header", {}, h("h2", {}, "Add people")),
    h("p", { class: "small" }, "Paste names and addresses, or a CSV with an email column. Nothing is mailed; new people start as “imported”."),
    box, field("Tag everyone with (optional)", tag),
    h("div", { class: "row", style: "margin-top:10px" },
      h("button", { on: { click: () => run(true) } }, "Preview"), h("button", { class: "primary", on: { click: () => run(false) } }, "Add"),), result);
}

function personRow(person, allLists, contextList) {
  const wrap = h("div", { class: "person" });
  const draw = () => {
    const tagInput = text("", { placeholder: "add tag", style: "max-width:150px" });
    const joinSelect = h("select", { style: "max-width:170px" }, h("option", { value: "" }, "Add to list…"),
      allLists.filter((n) => !(n in person.lists)).map((n) => h("option", { value: n }, n)));
    joinSelect.addEventListener("change", async () => {
      if (joinSelect.value && await attempt(() => api("contacts_join", { email: person.id, list_name: joinSelect.value }), "Added to list")) refresh();
    });
    wrap.replaceChildren(
      h("div", { class: "row spread" },
        h("div", {}, h("span", { class: "name" }, person.name || "(no name)"), " ", h("span", { class: "small" }, person.email || "(no email)"),
          person.linkedin_url ? h("span", {}, " · ", h("a", { href: person.linkedin_url, target: "_blank", rel: "noopener noreferrer" }, "LinkedIn")) : null),
        h("div", { class: "row" },
          h("button", { class: "quiet", on: { click: edit } }, "Edit"),
          h("button", { class: "quiet", on: { click: async () => {
            if (await attempt(() => api("contacts_do_not_mail", { email: person.id, clear: person.do_not_mail }), person.do_not_mail ? "Block lifted" : "Marked do-not-mail")) refresh();
          } } }, person.do_not_mail ? "Lift do-not-mail" : "Do not mail"),
          inlineConfirm("Delete", async () => { if (await attempt(() => api("contacts_delete", { contact: person.id }), "Deleted")) refresh(); }, "Delete this person everywhere"))),
      h("div", { class: "chips", style: "margin-top:6px" },
        Object.entries(person.lists).map(([listName, status]) => h("span", { class: `chip ${status}` }, listName, ": ", status,
          h("button", { title: "Remove from this list", on: { click: async () => { if (await attempt(() => api("contacts_leave", { email: person.id, list_name: listName }), "Removed from list")) refresh(); } } }, "×"))),
        person.tags.map((tag) => h("span", { class: "chip" }, tag, h("button", { title: "Remove tag", on: { click: async () => { if (await attempt(() => api("contacts_untag", { email: person.id, tag }))) refresh(); } } }, "×"))),
        person.do_not_mail ? h("span", { class: "chip bad" }, "do not mail") : null),
      h("div", { class: "row", style: "margin-top:8px" }, tagInput,
        h("button", { on: { click: async () => { if (tagInput.value.trim() && await attempt(() => api("contacts_tag", { email: person.id, tag: tagInput.value }))) refresh(); } } }, "Tag"), joinSelect));
  };
  const edit = () => {
    const name = text(person.name), email = text(person.email), linkedin = text(person.linkedin_url);
    wrap.replaceChildren(field("Name", name), field("Email", email), field("LinkedIn URL", linkedin),
      formButtons(async () => {
        if (await attempt(() => api("contacts_edit", { contact: person.id, email: email.value, name: name.value, linkedin_url: linkedin.value }), "Saved")) refresh();
      }, draw));
  };
  draw();
  return wrap;
}

async function contactsPage() {
  const { lists } = await api("lists_show");
  const listSelect = h("select", {}, h("option", { value: "" }, "Any list"), lists.map((l) => h("option", { value: l.name }, l.name)));
  const statusSelect = h("select", {}, h("option", { value: "" }, "Any status"), ["imported", "pending", "subscribed", "unsubscribed", "suppressed"].map((s) => h("option", { value: s }, s)));
  const tagInput = text("", { placeholder: "tag" });
  const results = h("div");
  const load = async () => {
    const found = await attempt(() => api("contacts_find", { list_name: listSelect.value || null, status: statusSelect.value || null, tag: tagInput.value.trim() || null, show_full_addresses: true }));
    if (!found) return;
    results.replaceChildren(h("p", { class: "small" }, plural(found.count, "person", "people")),
      found.contacts.length ? found.contacts.map((p) => personRow(p, lists.map((l) => l.name))) : h("p", { class: "empty" }, "No one matches."));
  };
  for (const control of [listSelect, statusSelect]) control.addEventListener("change", load);
  tagInput.addEventListener("change", load);
  await load();
  return h("div", {}, h("h1", {}, "Contacts"), h("p", { class: "sub" }, "Everyone you know, across all lists."),
    h("section", { class: "panel" }, h("div", { class: "cols" }, field("List", listSelect), field("Status", statusSelect)), field("Tag", tagInput)),
    importPanel(null),
    h("section", { class: "panel" }, results));
}

// ---- campaigns ---------------------------------------------------------------------------

async function campaignsPage() {
  const { campaigns, starters } = await api("campaigns_list");
  const name = text("", { placeholder: "e.g. october-update" });
  const starter = h("select", {}, starters.map((s) => h("option", { value: s, selected: s === "update" }, s)));
  const create = async () => {
    if (await attempt(() => api("campaigns_create", { name: name.value, starter: starter.value }), "Campaign created")) location.hash = `#/campaigns/${enc(name.value.trim())}`;
  };
  return h("div", {},
    h("h1", {}, "Campaigns"),
    h("p", { class: "sub" }, "Each campaign is one letter with its own values, lists and images."),
    h("section", { class: "panel" }, h("h2", {}, "New campaign"),
      h("div", { class: "cols" }, field("Name", name), field("Start from", starter)),
      h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "primary", on: { click: create } }, "Create campaign"))),
    campaigns.length ? h("div", { class: "grid" }, campaigns.map((c) => card(`#/campaigns/${enc(c.name)}`, c.name,
      `${c.subject || "(no subject)"} · ${c.kind} · ${c.lists.join(", ") || "no list"} · edited ${when(c.updated_at)}`))) : h("p", { class: "empty" }, "No campaigns yet. Create one from a starter above."));
}

function debounce(fn, ms) { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); }; }

function previewFrame() {
  const frame = h("iframe", { class: "preview", sandbox: "", title: "Email preview" });
  return { frame, show: (html) => { frame.srcdoc = html; } };
}

async function campaignPage(name) {
  const [{ campaign }, { lists }, { templates }, { settings }] = await Promise.all([
    api("campaigns_get", { name }), api("lists_show"), api("templates_list"), api("settings_get")]);
  const listNames = lists.map((l) => l.name);
  const root = h("div", {},
    h("p", {}, h("a", { href: "#/campaigns" }, "← All campaigns")),
    h("div", { class: "row spread" }, h("h1", {}, campaign.name),
      h("div", { class: "row" }, duplicateButton(campaign.name), inlineConfirm("Delete campaign", async () => {
        if (await attempt(() => api("campaigns_delete", { name }), "Campaign deleted")) location.hash = "#/campaigns";
      }, "Delete this campaign and its images"))),
    h("p", { class: "sub" }, `${campaign.kind === "confirmation" ? "Confirmation letter" : "Campaign"} · edited ${when(campaign.updated_at)}`));
  root.append(checkPanel(name), detailsSection(campaign, listNames, templates), valuesSection(campaign), senderSection(campaign, settings),
    bodySection(campaign), imagesSection(campaign), previewSection(campaign));
  return root;
}

function duplicateButton(name) {
  const newName = text(name + "-copy", { style: "max-width:200px" });
  const slot = h("span");
  const start = h("button", { on: { click: () => slot.replaceChildren(newName, h("button", { class: "primary", on: { click: async () => {
    if (await attempt(() => api("campaigns_duplicate", { name, new_name: newName.value }), "Duplicated")) location.hash = `#/campaigns/${enc(newName.value.trim())}`;
  } } }, "Duplicate"), h("button", { on: { click: () => slot.replaceChildren(start) } }, "Cancel")) } }, "Duplicate");
  slot.append(start);
  return slot;
}

function checkPanel(name) {
  const body = h("div", {}, h("p", { class: "small" }, "Checking…"));
  const run = async () => {
    const board = await attempt(() => api("campaigns_check", { name }));
    if (!board) { body.replaceChildren(h("p", { class: "small" }, "Could not check.")); return; }
    body.replaceChildren(
      h("div", { class: "big", style: "font-size:22px;font-weight:650" }, `Would reach ${plural(board.would_reach, "person", "people")} right now`),
      Object.keys(board.skipped).length ? h("p", { class: "small" }, "Not reached: " + Object.entries(board.skipped).map(([why, n]) => `${n} (${why})`).join(", ")) : null,
      h("p", { class: "small" }, `${plural(board.inline_images, "image")} · ${board.inline_image_kilobytes} KB · clicks ${board.track_clicks ? "tracked" : "not tracked"} · opens ${board.track_opens ? "tracked" : "not tracked"}`),
      board.problems.length ? h("div", { class: "notice warn" }, board.problems.map((p) => h("div", {}, "• " + p))) : h("div", { class: "notice good" }, "No problems found. Sending is not built yet; this only checks."));
  };
  run();
  window.checkAgain = run;
  return h("section", { class: "panel" }, h("header", {}, h("h2", {}, "Check"), h("button", { class: "quiet", on: { click: run } }, "Check again")), body);
}

function detailsSection(campaign, listNames, templates) {
  const view = h("div", {},
    h("p", {}, h("span", { class: "small" }, "Subject: "), campaign.subject || h("em", {}, "none")),
    h("p", {}, h("span", { class: "small" }, "Preview line: "), campaign.preview_text || "—"),
    h("p", {}, h("span", { class: "small" }, "Lists: "), campaign.lists.join(", ") || h("em", {}, "none (choose who it goes to)")),
    h("p", {}, h("span", { class: "small" }, "Only people tagged: "), campaign.tags.join(", ") || "everyone on the lists"),
    h("p", {}, h("span", { class: "small" }, "Template: "), campaign.template, " · kind: ", campaign.kind));
  const form = (done) => {
    const subject = text(campaign.subject), preview = text(campaign.preview_text), tags = text(campaign.tags.join(", "));
    const kind = h("select", {}, ["campaign", "confirmation"].map((k) => h("option", { value: k, selected: k === campaign.kind }, k)));
    const template = h("select", {}, templates.map((t) => h("option", { value: t.name, selected: t.name === campaign.template }, t.name)));
    const boxes = listNames.map((n) => h("label", { style: "display:inline-flex;gap:6px;margin-right:14px;align-items:center" },
      h("input", { type: "checkbox", value: n, checked: campaign.lists.includes(n) }), n));
    const clicks = h("input", { type: "checkbox", checked: campaign.track_clicks });
    const opens = h("input", { type: "checkbox", checked: campaign.track_opens });
    return h("div", {}, field("Subject (you can use values like {{ book_title }})", subject), field("Preview line (shown beside the subject in inboxes)", preview),
      h("label", {}, "Send to these lists"), h("div", {}, boxes.length ? boxes : h("span", { class: "small" }, "Create a list first.")),
      field("Only people with these tags (comma separated, optional)", tags),
      h("div", { class: "cols" }, field("Template", template), field("Kind (a confirmation letter goes to people who have not confirmed yet)", kind)),
      h("label", { style: "display:flex;gap:8px;align-items:center" }, clicks, "Track clicks"),
      h("label", { style: "display:flex;gap:8px;align-items:center" }, opens, "Track opens (unreliable: mail apps pre-load images)"),
      formButtons(async () => {
        const fields = { subject: subject.value, preview_text: preview.value, kind: kind.value, template: template.value, track_clicks: clicks.checked, track_opens: opens.checked,
          lists: boxes.map((b) => b.querySelector("input")).filter((i) => i.checked).map((i) => i.value),
          tags: tags.value.split(",").map((t) => t.trim()).filter(Boolean) };
        if (await attempt(() => api("campaigns_save", { name: campaign.name, fields }), "Saved")) done(true);
      }, () => done(false)));
  };
  return editable("Details", view, form, refresh);
}

function valuesSection(campaign) {
  const entries = Object.entries(campaign.variables);
  const view = entries.length ? h("div", {}, entries.map(([k, v]) => h("p", {}, h("code", {}, `{{ ${k} }}`), " = ", v || h("em", { class: "notice warn" }, "empty")))) : h("p", { class: "small" }, "No values yet.");
  const form = (done) => {
    const rows = h("div");
    const addRow = (key, value) => {
      const k = text(key, { placeholder: "name, e.g. book_title" }), v = text(value, { placeholder: "value" });
      const row = h("div", { class: "kv" }, k, v, h("button", { class: "quiet", on: { click: () => row.remove() } }, "Remove"));
      row._get = () => [k.value.trim(), v.value];
      rows.append(row);
    };
    entries.forEach(([k, v]) => addRow(k, v));
    if (!entries.length) addRow("book_title", "");
    return h("div", {}, h("p", { class: "small" }, "Use a value in the subject or body as {{ name }}. Names are lower-case letters, digits and underscores. {{ first_name }} and {{ sender_name }} are filled in for you."),
      rows, h("button", { on: { click: () => addRow("", "") } }, "Add a value"),
      formButtons(async () => {
        const variables = {};
        for (const row of rows.children) { const [k, v] = row._get(); if (k) variables[k] = v; }
        if (await attempt(() => api("campaigns_save", { name: campaign.name, fields: { variables } }), "Saved")) done(true);
      }, () => done(false)));
  };
  return editable("Values", view, form, refresh);
}

function senderSection(campaign, defaults) {
  const rows = SENDER_FIELDS.map(([key, label]) => [key, label, campaign[key], defaults[key]]);
  const view = h("div", {}, rows.map(([, label, own, fallback]) => h("p", {}, h("span", { class: "small" }, label + ": "), own || h("span", { class: "small" }, `${fallback || "not set"} (default from Settings)`))));
  const form = (done) => {
    const inputs = Object.fromEntries(rows.map(([key, , own, fallback]) => [key, text(own, { placeholder: fallback || "uses Settings" })]));
    return h("div", {}, h("p", { class: "small" }, "Leave a field empty to use the default from Settings."),
      rows.map(([key, label]) => field(label, inputs[key])),
      formButtons(async () => {
        const fields = Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.value]));
        if (await attempt(() => api("campaigns_save", { name: campaign.name, fields }), "Saved")) done(true);
      }, () => done(false)));
  };
  return editable("Sender for this campaign", view, form, refresh);
}

function bodySection(campaign) {
  const view = h("pre", { class: "plain" }, campaign.body || "(empty)");
  const form = (done) => {
    const box = h("textarea", { id: "body-editor" }, campaign.body);
    const { frame, show } = previewFrame();
    const message = h("div");
    const update = debounce(async () => {
      try {
        const out = await api("campaigns_preview", { name: campaign.name, write_files: false, draft: { body: box.value } });
        show(out.html); message.replaceChildren(out.warnings.length ? h("div", { class: "notice warn" }, out.warnings.join(" · ")) : "");
      } catch (error) { message.replaceChildren(h("div", { class: "notice bad" }, error.message)); }
    }, 450);
    box.addEventListener("input", update);
    update();
    return h("div", {}, h("p", { class: "small" }, "Write in Markdown: **bold**, *italic*, # headings, - lists, tables, [links](https://…) and images from the Images section below. Raw HTML is not used."),
      h("div", { class: "cols" }, h("div", {}, box), frame), message,
      formButtons(async () => { if (await attempt(() => api("campaigns_save", { name: campaign.name, fields: { body: box.value } }), "Saved")) done(true); }, () => done(false)));
  };
  return editable("Letter", view, form, refresh);
}

function imagesSection(campaign) {
  const picker = h("input", { type: "file", accept: "image/*" });
  const alt = text("", { placeholder: "describe the picture (alt text)" });
  const upload = async () => {
    const file = picker.files[0];
    if (!file) { toast("Choose a picture first.", true); return; }
    const filename = file.name.replace(/[^A-Za-z0-9_.-]/g, "-");
    const response = await fetch(`/upload/${enc(campaign.name)}/${enc(filename)}`, { method: "PUT", headers: { "Content-Type": file.type || "image/png", "X-Mailerlm": "1" }, body: file });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) { toast(body.error || "Upload failed", true); return; }
    toast("Uploaded. Use it in the letter with the snippet shown.");
    refresh();
  };
  const body = h("div", {},
    campaign.images.length ? campaign.images.map((image) => {
      const snippet = `![${alt.value || "describe the picture"}](${image.filename})`;
      return h("div", { class: "person row spread" },
        h("div", { class: "row" }, h("img", { class: "thumb", src: `/image/${enc(campaign.name)}/${enc(image.filename)}`, alt: image.filename }),
          h("div", {}, h("div", {}, image.filename), h("div", { class: "small" }, `${Math.round(image.size / 1024)} KB original · it is shrunk for the email`),
            h("code", {}, `![describe the picture](${image.filename})`))),
        h("div", { class: "row" }, h("button", { on: { click: () => {
          const editor = document.getElementById("body-editor");
          if (!editor) { toast("Open the Letter section with Edit first.", true); return; }
          editor.setRangeText(`\n\n![${alt.value || "describe the picture"}](${image.filename})\n\n`, editor.selectionStart, editor.selectionEnd, "end");
          editor.dispatchEvent(new Event("input"));
        } } }, "Insert into letter"),
        inlineConfirm("Remove", async () => { if (await attempt(() => api("campaigns_image_remove", { name: campaign.name, filename: image.filename }), "Removed")) refresh(); }, "Remove this image")));
    }) : h("p", { class: "small" }, "No images yet."),
    h("div", { class: "cols", style: "margin-top:12px" }, field("Add a picture (any size; it is resized and compressed for email)", picker), field("Alt text used when inserting", alt)),
    h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "primary", on: { click: upload } }, "Upload")));
  return h("section", { class: "panel" }, h("header", {}, h("h2", {}, "Images")), body);
}

function previewSection(campaign) {
  const first = text("Friend", { style: "max-width:200px" });
  const { frame, show } = previewFrame();
  const plain = h("pre", { class: "plain" });
  const message = h("div");
  const run = async () => {
    try {
      const out = await api("campaigns_preview", { name: campaign.name, first_name: first.value, write_files: false });
      show(out.html); plain.textContent = out.text;
      message.replaceChildren(out.warnings.length ? h("div", { class: "notice warn" }, out.warnings.join(" · ")) : "");
    } catch (error) { message.replaceChildren(h("div", { class: "notice bad" }, error.message)); }
  };
  run();
  return h("section", { class: "panel" }, h("header", {}, h("h2", {}, "Preview"), h("div", { class: "row" }, first, h("button", { on: { click: run } }, "Refresh"))),
    message, frame, h("details", { style: "margin-top:10px" }, h("summary", {}, "Plain-text version"), plain));
}

// ---- templates ---------------------------------------------------------------------------

async function templatesPage() {
  const { templates } = await api("templates_list");
  return h("div", {}, h("h1", {}, "Templates"),
    h("p", { class: "sub" }, "The frame every letter is poured into: colours, fonts, spacing and the footer. Campaigns pick one."),
    h("div", { class: "grid" }, templates.map((t) => card(`#/templates/${enc(t.name)}`, t.name, `${t.built_in ? "built-in · " : ""}used by ${plural(t.campaigns_using, "campaign")} · edited ${when(t.updated_at)}`))));
}

async function templatePage(name) {
  const [{ template }, { campaigns }] = await Promise.all([api("templates_get", { name }), api("campaigns_list")]);
  const actions = h("div", { class: "row" });
  const newName = text(name + " copy", { style: "max-width:200px" });
  actions.append(
    newName, h("button", { on: { click: async () => { if (await attempt(() => api("templates_duplicate", { name, new_name: newName.value }), "Duplicated")) location.hash = `#/templates/${enc(newName.value.trim())}`; } } }, "Duplicate"),
    template.built_in ? inlineConfirm("Reset to original", async () => { if (await attempt(() => api("templates_reset", { name }), "Reset")) refresh(); }, "Replace your edits with the original") :
      inlineConfirm("Delete template", async () => { if (await attempt(() => api("templates_delete", { name }), "Deleted")) location.hash = "#/templates"; }, "Delete this template"));
  const html = h("textarea", { style: "min-height:340px" }, template.html), textFrame = h("textarea", { class: "short" }, template.text);
  const sample = h("select", {}, campaigns.map((c) => h("option", { value: c.name }, c.name)));
  const { frame, show } = previewFrame();
  const message = h("div");
  const run = async () => {
    if (!sample.value) { message.replaceChildren(h("p", { class: "small" }, "Create a campaign to preview this template with.")); return; }
    try { const out = await api("campaigns_preview", { name: sample.value, write_files: false, draft: { frame_html: html.value, frame_text: textFrame.value } }); show(out.html); message.replaceChildren(""); }
    catch (error) { message.replaceChildren(h("div", { class: "notice bad" }, error.message)); }
  };
  const debounced = debounce(run, 500);
  html.addEventListener("input", debounced); textFrame.addEventListener("input", debounced); sample.addEventListener("change", run);
  run();
  return h("div", {}, h("p", {}, h("a", { href: "#/templates" }, "← All templates")), h("h1", {}, template.name), h("p", { class: "sub" }, template.built_in ? "Built-in: you can edit it and reset it." : "Your template."),
    h("section", { class: "panel" }, h("header", {}, h("h2", {}, "Frame")),
      h("p", { class: "small" }, "Keep {{ body_html | safe }} (HTML) and {{ body_text }} (text) where the letter goes. Also available: {{ subject }}, {{ preview_text }}, {{ unsubscribe_line }}, {{ sender_name }}, {{ postal_address }}."),
      field("HTML", html), field("Plain text", textFrame),
      h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "primary", on: { click: async () => { if (await attempt(() => api("templates_save", { name, html: html.value, text: textFrame.value }), "Saved")) refresh(); } } }, "Save template")),
      h("div", { style: "margin-top:12px" }, actions)),
    h("section", { class: "panel" }, h("header", {}, h("h2", {}, "Preview"), sample), message, frame));
}

render();
