const bridge = window.AstrBotPluginPage;

const $ = (selector) => document.querySelector(selector);
const refs = {
  appShell: $("#appShell"),
  sidebar: $("#sidebar"),
  mobileFilterToggle: $("#mobileFilterToggle"),
  mobileFilterState: $("#mobileFilterState"),
  connection: $("#connectionState"),
  refresh: $("#refreshButton"),
  newEntry: $("#newEntryButton"),
  emptyCreate: $("#emptyCreateButton"),
  search: $("#searchInput"),
  status: $("#statusFilter"),
  templateFilter: $("#templateFilter"),
  folder: $("#folderFilter"),
  tag: $("#tagFilter"),
  clearFilters: $("#clearFiltersButton"),
  sort: $("#sortSelect"),
  pageSize: $("#pageSizeSelect"),
  density: $("#densityButton"),
  collapseAll: $("#collapseAllButton"),
  selectPage: $("#selectPageButton"),
  stats: $("#statsGrid"),
  resultTitle: $("#resultTitle"),
  resultCaption: $("#resultCaption"),
  tree: $("#tree"),
  list: $("#entryList"),
  empty: $("#emptyState"),
  pagination: $("#pagination"),
  bulkBar: $("#bulkBar"),
  selectedCount: $("#selectedCount"),
  bulkAction: $("#bulkAction"),
  bulkValue: $("#bulkValue"),
  bulkApply: $("#bulkApplyButton"),
  exportSelected: $("#exportSelectedButton"),
  clearSelection: $("#clearSelectionButton"),
  importButton: $("#importButton"),
  exportYaml: $("#exportYamlButton"),
  exportJson: $("#exportJsonButton"),
  drawer: $("#editorDrawer"),
  drawerScrim: $("#drawerScrim"),
  editorTitle: $("#editorTitle"),
  closeEditor: $("#closeEditorButton"),
  cancelEditor: $("#cancelEditorButton"),
  deleteEntry: $("#deleteEntryButton"),
  duplicateEntry: $("#duplicateEntryButton"),
  entryForm: $("#entryForm"),
  formError: $("#formError"),
  saveEntry: $("#saveEntryButton"),
  template: $("#templateInput"),
  enabled: $("#enabledInput"),
  name: $("#nameInput"),
  folderInput: $("#folderInput"),
  tags: $("#tagsInput"),
  priority: $("#priorityInput"),
  probability: $("#probabilityInput"),
  duration: $("#durationInput"),
  times: $("#timesInput"),
  keywords: $("#keywordsInput"),
  keywordMode: $("#keywordModeInput"),
  cron: $("#cronInput"),
  scope: $("#scopeInput"),
  content: $("#contentInput"),
  importDialog: $("#importDialog"),
  importForm: $("#importForm"),
  importFile: $("#importFileInput"),
  importFileName: $("#importFileName"),
  importStrategy: $("#importStrategyInput"),
  importError: $("#importError"),
  closeImport: $("#closeImportButton"),
  cancelImport: $("#cancelImportButton"),
  confirmImport: $("#confirmImportButton"),
  confirmDialog: $("#confirmDialog"),
  confirmTitle: $("#confirmTitle"),
  confirmMessage: $("#confirmMessage"),
  confirmHint: $("#confirmHint"),
  cancelConfirm: $("#cancelConfirmButton"),
  acceptConfirm: $("#acceptConfirmButton"),
  toastRegion: $("#toastRegion"),
};

const PREF_PREFIX = "worldtree.";
const DEFAULT_SORT = "priority";
const DEFAULT_PAGE_SIZE = 30;

const SORT_LABELS = {
  priority: "优先级 · 小到大",
  priority_desc: "优先级 · 大到小",
  template: "条目类型",
  folder: "文件夹",
  name: "名称",
  updated: "最近更新",
  enabled: "启用状态",
};

const PRIORITY_TIERS = [
  { max: 10, key: "tier-core", label: "树心 · 优先级 ≤ 10" },
  { max: 50, key: "tier-trunk", label: "主干 · 优先级 11 – 50" },
  { max: 100, key: "tier-branch", label: "枝条 · 优先级 51 – 100" },
  { max: Infinity, key: "tier-leaf", label: "新叶 · 优先级 > 100" },
];

const UPDATED_TIERS = [
  { max: 86400, key: "fresh-day", label: "最近 24 小时" },
  { max: 604800, key: "fresh-week", label: "最近 7 天" },
  { max: 2592000, key: "fresh-month", label: "最近 30 天" },
  { max: Infinity, key: "fresh-older", label: "更早" },
];

// the canopy shows one trunk-ring total plus a row of leaf chips
const STAT_TOTAL = { key: "total", label: "全部条目", status: "all" };
const STAT_CELLS = [
  { key: "enabled", label: "启用中", status: "enabled" },
  { key: "disabled", label: "已停用", status: "disabled" },
  { key: "scheduled", label: "日程", status: "scheduled" },
  { key: "scoped", label: "范围限定", status: "scoped" },
  { key: "folders", label: "文件夹" },
];

const VALUE_ACTIONS = ["set_folder", "add_tag", "remove_tag"];

let lastFocusedElement = null;
let confirmationResolver = null;
let searchTimer = null;

const state = {
  revision: 0,
  templates: [],
  entries: [],
  groups: [],
  stats: {},
  selected: new Set(),
  collapsed: new Set(),
  expanded: new Set(),
  contentCache: new Map(),
  editingId: null,
  editorSequence: 0,
  loading: false,
  loadSequence: 0,
  density: "cosy",
  filters: {
    q: "",
    status: "all",
    template: "",
    folder: "",
    tag: "",
    sort: DEFAULT_SORT,
    page: 1,
    page_size: DEFAULT_PAGE_SIZE,
  },
  pagination: { page: 1, page_size: DEFAULT_PAGE_SIZE, total: 0, total_pages: 1 },
};

/* ---------------------------------------------------------------- helpers */

function readPref(key, fallback) {
  try {
    const stored = window.localStorage.getItem(PREF_PREFIX + key);
    return stored === null ? fallback : stored;
  } catch {
    return fallback;
  }
}

function writePref(key, value) {
  try {
    window.localStorage.setItem(PREF_PREFIX + key, String(value));
  } catch {
    /* Private-mode browsers may refuse storage; preferences are optional. */
  }
}

function create(tag, className = "", text = undefined) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function badge(text, className) {
  return create("span", className, text);
}

function setConnection(mode, message) {
  refs.connection.classList.remove("online", "error");
  if (mode) refs.connection.classList.add(mode);
  refs.connection.textContent = message;
}

function showToast(message, type = "success") {
  const toast = create("div", `toast ${type === "success" ? "" : type}`, message);
  refs.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 4600);
}

function asErrorMessage(error) {
  if (error instanceof Error) return error.message || "请求失败";
  return String(error || "请求失败");
}

function isStale(message) {
  return message.includes("已被其他操作更新");
}

function settleConfirmation(confirmed) {
  const resolve = confirmationResolver;
  confirmationResolver = null;
  if (refs.confirmDialog.open) refs.confirmDialog.close();
  if (resolve) resolve(Boolean(confirmed));
}

function requestConfirmation({ title, message, hint, confirmLabel = "确认" }) {
  if (confirmationResolver) settleConfirmation(false);
  refs.confirmTitle.textContent = title;
  refs.confirmMessage.textContent = message;
  refs.confirmHint.textContent = hint;
  refs.acceptConfirm.textContent = confirmLabel;

  return new Promise((resolve) => {
    confirmationResolver = resolve;
    refs.confirmDialog.showModal();
    window.requestAnimationFrame(() => refs.cancelConfirm.focus());
  });
}

// Only swap the caption when one is supplied, so icon-only controls such as the
// enable/disable switch keep their shape while a request is in flight.
function updateBusy(button, busy, busyText = "") {
  if (!button) return;
  if (busy) {
    if (busyText) {
      if (!("originalText" in button.dataset)) button.dataset.originalText = button.textContent;
      button.textContent = busyText;
    }
    button.disabled = true;
    button.classList.add("is-busy");
  } else {
    if ("originalText" in button.dataset) {
      button.textContent = button.dataset.originalText;
      delete button.dataset.originalText;
    }
    button.disabled = false;
    button.classList.remove("is-busy");
  }
}

function splitList(value) {
  return value
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function templateFor(key) {
  return state.templates.find((template) => template.key === key);
}

function templateLabel(key) {
  return templateFor(key)?.label || key || "常规条目";
}

function setFormError(message = "") {
  refs.formError.hidden = !message;
  refs.formError.textContent = message;
}

function setImportError(message = "") {
  refs.importError.hidden = !message;
  refs.importError.textContent = message;
}

function isTypingTarget(target) {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

function relativeTime(seconds) {
  if (!seconds) return "";
  const delta = Math.max(0, Math.floor(Date.now() / 1000) - seconds);
  if (delta < 60) return "刚刚";
  if (delta < 3600) return `${Math.floor(delta / 60)} 分钟前`;
  if (delta < 86400) return `${Math.floor(delta / 3600)} 小时前`;
  if (delta < 2592000) return `${Math.floor(delta / 86400)} 天前`;
  return new Date(seconds * 1000).toLocaleDateString();
}

/* ------------------------------------------------------------- filter UI */

function setSelectOptions(select, firstLabel, values, currentValue) {
  const previous = currentValue ?? select.value;
  select.replaceChildren();
  const first = create("option", "", firstLabel);
  first.value = "";
  select.append(first);
  for (const value of values) {
    const option = create("option", "", value);
    option.value = value;
    select.append(option);
  }
  select.value = values.includes(previous) ? previous : "";
}

function syncTemplateFilter() {
  const previous = state.filters.template;
  refs.templateFilter.replaceChildren();
  const first = create("option", "", "全部");
  first.value = "";
  refs.templateFilter.append(first);
  for (const template of state.templates) {
    const option = create("option", "", template.label);
    option.value = template.key;
    refs.templateFilter.append(option);
  }
  const keys = state.templates.map((template) => template.key);
  refs.templateFilter.value = keys.includes(previous) ? previous : "";
  state.filters.template = refs.templateFilter.value;
}

function syncFilterControls(facets) {
  syncTemplateFilter();
  setSelectOptions(refs.folder, "全部", facets.folders || [], state.filters.folder);
  setSelectOptions(refs.tag, "全部", facets.tags || [], state.filters.tag);
  state.filters.folder = refs.folder.value;
  state.filters.tag = refs.tag.value;
}

function statFilterButton(cell, extraClass) {
  const active = state.filters.status === cell.status;
  const button = create("button", `${extraClass} stat-action${active ? " is-active" : ""}`);
  button.type = "button";
  button.dataset.status = cell.status;
  button.setAttribute("aria-pressed", String(active));
  button.title = `按“${cell.label}”筛选`;
  button.addEventListener("click", () => {
    refs.status.value = cell.status;
    updateFilters();
  });
  return button;
}

function renderStats(stats) {
  state.stats = stats || {};
  refs.stats.replaceChildren();

  const total = statFilterButton(STAT_TOTAL, "canopy-total");
  total.append(
    create("strong", "", String(state.stats[STAT_TOTAL.key] ?? 0)),
    create("span", "", STAT_TOTAL.label),
    create("i", "canopy-rings", ""),
  );
  refs.stats.append(total);

  const leaves = create("div", "leaf-row");
  for (const cell of STAT_CELLS) {
    const value = String(state.stats[cell.key] ?? 0);
    const node = cell.status
      ? statFilterButton(cell, "leaf-chip")
      : create("div", "leaf-chip is-static");
    node.append(create("span", "", cell.label), create("strong", "", value));
    leaves.append(node);
  }
  refs.stats.append(leaves);
}

/* -------------------------------------------------------------- grouping */

function groupFor(entry, sort) {
  if (sort === "template") {
    return { key: `tpl:${entry.template}`, label: templateLabel(entry.template), template: entry.template };
  }
  if (sort === "folder") {
    const folder = entry.folder || "";
    return { key: `dir:${folder}`, label: folder || "未归档" };
  }
  if (sort === "enabled") {
    return entry.enabled
      ? { key: "state:on", label: "启用中" }
      : { key: "state:off", label: "已停用" };
  }
  if (sort === "updated") {
    const age = Math.max(0, Math.floor(Date.now() / 1000) - (entry.updated_at || 0));
    const tier = UPDATED_TIERS.find((item) => age <= item.max) || UPDATED_TIERS.at(-1);
    return { key: `age:${tier.key}`, label: tier.label };
  }
  if (sort === "name") {
    return { key: "all", label: "全部条目 · 按名称排列" };
  }
  const tier = PRIORITY_TIERS.find((item) => (entry.priority ?? 0) <= item.max) || PRIORITY_TIERS.at(-1);
  return { key: `pri:${tier.key}`, label: tier.label };
}

// The backend already returns the page in sorted order, so merging neighbouring
// entries with the same group key is enough - no second sort pass on the client.
function buildGroups(entries, sort) {
  const groups = [];
  for (const entry of entries) {
    const info = groupFor(entry, sort);
    const last = groups.at(-1);
    if (last && last.key === info.key) {
      last.entries.push(entry);
      continue;
    }
    groups.push({ key: info.key, label: info.label, template: info.template || "", entries: [entry] });
  }
  return groups;
}

/* ------------------------------------------------------------ entry menus */

function closeAllMenus(except = null) {
  for (const menu of refs.list.querySelectorAll(".entry-menu.is-open")) {
    if (menu === except) continue;
    menu.classList.remove("is-open");
    menu.querySelector(".menu-trigger")?.setAttribute("aria-expanded", "false");
  }
}

function menuItem(label, handler, className = "") {
  const item = create("button", `menu-item ${className}`.trim(), label);
  item.type = "button";
  item.setAttribute("role", "menuitem");
  item.addEventListener("click", (event) => {
    event.stopPropagation();
    closeAllMenus();
    handler();
  });
  return item;
}

function entryMenu(entry) {
  const wrapper = create("div", "entry-menu");
  const trigger = create("button", "icon-button menu-trigger", "⋯");
  trigger.type = "button";
  trigger.title = "更多操作";
  trigger.setAttribute("aria-label", `条目 ${entry.name} 的更多操作`);
  trigger.setAttribute("aria-haspopup", "menu");
  trigger.setAttribute("aria-expanded", "false");
  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    const open = wrapper.classList.contains("is-open");
    closeAllMenus(wrapper);
    wrapper.classList.toggle("is-open", !open);
    trigger.setAttribute("aria-expanded", String(!open));
  });

  const pop = create("div", "menu-pop");
  pop.setAttribute("role", "menu");
  pop.append(
    menuItem("复制为新条目", () => duplicateEntry(entry.id)),
    menuItem("仅导出此条目", () => exportEntries("yaml", null, [entry.id])),
    menuItem("删除条目", () => deleteEntry(entry.id, entry.name), "danger"),
  );

  wrapper.append(trigger, pop);
  return wrapper;
}

/* ----------------------------------------------------------- entry cards */

async function toggleEntryPreview(entry, card, button, preview) {
  const expanded = state.expanded.has(entry.id);
  if (expanded) {
    state.expanded.delete(entry.id);
    preview.classList.remove("is-expanded");
    preview.textContent = entry.preview || "（无内容预览）";
    button.textContent = "展开全文";
    button.setAttribute("aria-expanded", "false");
    return;
  }
  state.expanded.add(entry.id);
  button.setAttribute("aria-expanded", "true");
  if (!state.contentCache.has(entry.id)) {
    updateBusy(button, true, "读取中…");
    try {
      const result = await bridge.apiGet(`entry/${entry.id}`);
      state.contentCache.set(entry.id, result.entry.content || "");
    } catch (error) {
      state.expanded.delete(entry.id);
      button.setAttribute("aria-expanded", "false");
      showToast(asErrorMessage(error), "error");
      return;
    } finally {
      updateBusy(button, false);
    }
  }
  if (!state.expanded.has(entry.id) || !card.isConnected) return;
  preview.textContent = state.contentCache.get(entry.id) || "（此条目没有内容）";
  preview.classList.add("is-expanded");
  button.textContent = "收起全文";
}

function cardFor(entry) {
  const card = create("article", "entry-card");
  card.dataset.entryId = entry.id;
  card.dataset.template = entry.template || "common";
  if (!entry.enabled) card.classList.add("is-disabled");
  if (state.selected.has(entry.id)) card.classList.add("selected");

  const checkbox = create("input", "entry-select");
  checkbox.type = "checkbox";
  checkbox.checked = state.selected.has(entry.id);
  checkbox.setAttribute("aria-label", `选择条目 ${entry.name}`);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) state.selected.add(entry.id);
    else state.selected.delete(entry.id);
    card.classList.toggle("selected", checkbox.checked);
    renderBulkBar();
    renderSelectPageButton();
  });

  const main = create("div", "entry-main");

  const title = create("div", "entry-title");
  const heading = create("h3");
  const open = create("button", "entry-open", entry.name);
  open.type = "button";
  open.title = `编辑“${entry.name}”`;
  open.addEventListener("click", () => openEditor(entry.id));
  heading.append(open);
  title.append(heading);
  title.append(badge(templateLabel(entry.template), "template-pill"));
  title.append(
    badge(entry.enabled ? "启用" : "停用", `status-badge ${entry.enabled ? "enabled" : "disabled"}`),
  );
  if (entry.folder) title.append(badge(entry.folder, "folder-pill"));

  const meta = create("div", "entry-meta");
  meta.append(badge(`优先级 ${entry.priority}`, "trigger-pill"));
  const keywordCount = (entry.keywords || []).length;
  if (keywordCount) meta.append(badge(`${keywordCount} 个关键词`, "trigger-pill"));
  if (entry.cron) meta.append(badge(`Cron ${entry.cron}`, "trigger-pill"));
  if (typeof entry.probability === "number" && entry.probability < 1) {
    meta.append(badge(`概率 ${Math.round(entry.probability * 100)}%`, "trigger-pill"));
  }
  if ((entry.scope || []).length) meta.append(badge("范围限定", "trigger-pill"));
  for (const tag of entry.tags || []) meta.append(badge(`#${tag}`, "tag"));
  const updated = relativeTime(entry.updated_at);
  if (updated) meta.append(badge(`更新于 ${updated}`, "meta-note"));

  const preview = create("p", "entry-preview", entry.preview || "（无内容预览）");
  main.append(title, meta, preview);

  const expand = create("button", "entry-expand", "展开全文");
  expand.type = "button";
  expand.setAttribute("aria-expanded", "false");
  expand.addEventListener("click", () => toggleEntryPreview(entry, card, expand, preview));
  main.append(expand);

  if (state.expanded.has(entry.id) && state.contentCache.has(entry.id)) {
    preview.textContent = state.contentCache.get(entry.id) || "（此条目没有内容）";
    preview.classList.add("is-expanded");
    expand.textContent = "收起全文";
    expand.setAttribute("aria-expanded", "true");
  }

  const actions = create("div", "entry-actions");
  const toggle = create("button", `toggle ${entry.enabled ? "on" : ""}`.trim());
  toggle.type = "button";
  toggle.title = entry.enabled ? "停用此条目" : "启用此条目";
  toggle.setAttribute("aria-label", toggle.title);
  toggle.setAttribute("aria-pressed", String(entry.enabled));
  toggle.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleEntry(entry, toggle);
  });
  const edit = create("button", "button button-ghost button-compact", "编辑");
  edit.type = "button";
  edit.addEventListener("click", () => openEditor(entry.id));
  actions.append(toggle, edit, entryMenu(entry));

  card.append(checkbox, main, actions);
  return card;
}

function refreshCard(entry) {
  const existing = refs.list.querySelector(`.entry-card[data-entry-id="${entry.id}"]`);
  if (!existing) return;
  existing.replaceWith(cardFor(entry));
}

function branchFor(group) {
  const section = create("section", "branch");
  section.dataset.key = group.key;
  if (group.template) section.dataset.template = group.template;
  const collapsed = state.collapsed.has(group.key);
  if (collapsed) section.classList.add("collapsed");

  const head = create("button", "branch-head");
  head.type = "button";
  head.setAttribute("aria-expanded", String(!collapsed));
  head.append(
    create("span", "branch-node"),
    create("span", "branch-label", group.label),
    create("span", "branch-count", String(group.entries.length)),
    create("span", "branch-chevron", "▾"),
  );

  const body = create("div", "branch-body");
  for (const entry of group.entries) body.append(cardFor(entry));
  head.setAttribute("aria-controls", `branch-${group.key.replace(/[^\w-]/g, "_")}`);
  body.id = head.getAttribute("aria-controls");

  head.addEventListener("click", () => {
    const nowCollapsed = !section.classList.contains("collapsed");
    section.classList.toggle("collapsed", nowCollapsed);
    head.setAttribute("aria-expanded", String(!nowCollapsed));
    if (nowCollapsed) state.collapsed.add(group.key);
    else state.collapsed.delete(group.key);
    updateCollapseAllButton();
  });

  section.append(head, body);
  return section;
}

/* -------------------------------------------------------------- rendering */

function renderEntries() {
  closeAllMenus();
  const entries = state.entries;
  state.groups = buildGroups(entries, state.filters.sort);
  refs.list.replaceChildren(...state.groups.map(branchFor));
  refs.empty.hidden = entries.length !== 0;
  refs.tree.hidden = entries.length === 0;

  const total = state.pagination.total || 0;
  refs.resultTitle.textContent = total ? `共 ${total} 个条目` : "没有匹配的条目";
  const captions = [];
  if (state.filters.q) captions.push(`搜索“${state.filters.q}”`);
  if (state.filters.template) captions.push(templateLabel(state.filters.template));
  captions.push(`按 ${SORT_LABELS[state.filters.sort] || state.filters.sort} 排列`);
  // when paginated, say where we are so the page buttons are not the only clue
  const totalPages = state.pagination.total_pages || 1;
  if (totalPages > 1) captions.push(`第 ${state.pagination.page || 1} / ${totalPages} 页`);
  refs.resultCaption.textContent = captions.join(" · ");

  renderPagination();
  renderBulkBar();
  renderSelectPageButton();
  updateCollapseAllButton();
}

function pageButton(label, page, { current = false, disabled = false, ariaLabel = "" } = {}) {
  const button = create("button", `page-button ${current ? "current" : ""}`.trim(), label);
  button.type = "button";
  button.disabled = disabled;
  // only the active page carries aria-current; arrows are labelled instead
  if (current) button.setAttribute("aria-current", "page");
  button.setAttribute("aria-label", ariaLabel || `第 ${page} 页`);
  button.addEventListener("click", () => {
    if (page !== state.filters.page) {
      state.filters.page = page;
      state.selected.clear();
      loadEntries();
    }
  });
  return button;
}

function renderPagination() {
  refs.pagination.replaceChildren();
  const { page, total_pages: totalPages } = state.pagination;
  // hide the row entirely on a single page so its margin does not leave a gap
  refs.pagination.hidden = totalPages <= 1;
  if (totalPages <= 1) return;
  refs.pagination.append(
    pageButton("‹", Math.max(1, page - 1), { disabled: page === 1, ariaLabel: "上一页" }),
  );
  const pages = new Set([1, totalPages, page - 1, page, page + 1]);
  const ordered = [...pages].filter((item) => item >= 1 && item <= totalPages).sort((a, b) => a - b);
  let previous = 0;
  for (const item of ordered) {
    if (item - previous > 1) refs.pagination.append(create("span", "page-gap", "…"));
    refs.pagination.append(pageButton(String(item), item, { current: item === page }));
    previous = item;
  }
  refs.pagination.append(
    pageButton("›", Math.min(totalPages, page + 1), {
      disabled: page === totalPages,
      ariaLabel: "下一页",
    }),
  );
}

function renderBulkBar() {
  const count = state.selected.size;
  refs.bulkBar.hidden = count === 0;
  refs.selectedCount.textContent = `已选择 ${count} 项`;
  updateBulkValueState();
}

function renderSelectPageButton() {
  const ids = state.entries.map((entry) => entry.id);
  const allSelected = ids.length > 0 && ids.every((id) => state.selected.has(id));
  refs.selectPage.disabled = ids.length === 0;
  refs.selectPage.textContent = allSelected ? "取消本页" : "选择本页";
  refs.selectPage.setAttribute("aria-pressed", String(allSelected));
}

function toggleCurrentPageSelection() {
  const ids = state.entries.map((entry) => entry.id);
  const allSelected = ids.length > 0 && ids.every((id) => state.selected.has(id));
  for (const id of ids) {
    if (allSelected) state.selected.delete(id);
    else state.selected.add(id);
  }
  renderEntries();
}

function toggleMobileFilters() {
  const isOpen = refs.sidebar.classList.toggle("mobile-open");
  refs.mobileFilterToggle.setAttribute("aria-expanded", String(isOpen));
  refs.mobileFilterState.textContent = isOpen ? "收起" : "展开";
}

function updateBulkValueState() {
  const needsValue = VALUE_ACTIONS.includes(refs.bulkAction.value);
  refs.bulkValue.disabled = !needsValue;
  refs.bulkValue.placeholder = needsValue ? "文件夹或标签" : "此操作无需值";
}

function updateCollapseAllButton() {
  const keys = state.groups.map((group) => group.key);
  const allCollapsed = keys.length > 0 && keys.every((key) => state.collapsed.has(key));
  refs.collapseAll.disabled = keys.length === 0;
  refs.collapseAll.textContent = allCollapsed ? "展开全部" : "收起全部";
  refs.collapseAll.setAttribute("aria-pressed", String(allCollapsed));
}

function toggleAllBranches() {
  const keys = state.groups.map((group) => group.key);
  if (!keys.length) return;
  const allCollapsed = keys.every((key) => state.collapsed.has(key));
  if (allCollapsed) state.collapsed.clear();
  else for (const key of keys) state.collapsed.add(key);
  renderEntries();
}

function applyDensity() {
  refs.appShell.dataset.density = state.density;
  const compact = state.density === "compact";
  refs.density.textContent = compact ? "舒适视图" : "紧凑视图";
  refs.density.setAttribute("aria-pressed", String(compact));
}

function toggleDensity() {
  state.density = state.density === "compact" ? "cosy" : "compact";
  writePref("density", state.density);
  applyDensity();
}

/* ------------------------------------------------------------ data access */

async function loadEntries({ announce = false } = {}) {
  if (!bridge) return;
  const sequence = ++state.loadSequence;
  state.loading = true;
  refs.list.setAttribute("aria-busy", "true");
  updateBusy(refs.refresh, true, "读取中…");
  try {
    const result = await bridge.apiGet("entries", state.filters);
    if (sequence !== state.loadSequence) return;
    state.revision = result.revision;
    state.entries = result.entries || [];
    state.pagination = result.pagination || state.pagination;
    state.templates = result.templates || state.templates;
    if (result.pagination?.sort) {
      state.filters.sort = result.pagination.sort;
      refs.sort.value = state.filters.sort;
    }
    syncFilterControls(result.facets || { folders: [], tags: [] });
    renderStats(result.stats || {});
    renderEntries();
    setConnection("online", "已同步");
    if (announce) showToast("条目已刷新");
  } catch (error) {
    if (sequence !== state.loadSequence) return;
    setConnection("error", "连接失败");
    refs.resultTitle.textContent = "无法读取条目";
    showToast(asErrorMessage(error), "error");
  } finally {
    if (sequence === state.loadSequence) {
      state.loading = false;
      refs.list.setAttribute("aria-busy", "false");
      updateBusy(refs.refresh, false);
    }
  }
}

async function loadDiagnostics() {
  try {
    const result = await bridge.apiGet("diagnostics");
    const invalid = Object.keys(result.invalid_cron_entries || {});
    if (invalid.length) {
      showToast(`有 ${invalid.length} 个 Cron 条目格式无效，已跳过调度。请打开相应条目修正。`, "warning");
    }
  } catch {
    // Diagnostics are helpful but should not block management if unavailable.
  }
}

/* ---------------------------------------------------------------- editor */

function openDrawer() {
  lastFocusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  refs.drawer.setAttribute("aria-hidden", "false");
  refs.appShell.inert = true;
  document.body.style.overflow = "hidden";
  window.setTimeout(() => refs.name.focus(), 180);
}

function closeEditor() {
  state.editorSequence += 1;
  refs.drawer.setAttribute("aria-hidden", "true");
  refs.appShell.inert = false;
  document.body.style.overflow = "";
  state.editingId = null;
  refs.deleteEntry.hidden = true;
  refs.duplicateEntry.hidden = true;
  setFormError();
  lastFocusedElement?.focus();
  lastFocusedElement = null;
}

function fillTemplateOptions(selected = "common") {
  refs.template.replaceChildren();
  for (const template of state.templates) {
    const option = create("option", "", template.label);
    option.value = template.key;
    refs.template.append(option);
  }
  refs.template.value = state.templates.some((item) => item.key === selected) ? selected : "common";
}

function setFormValues(entry) {
  fillTemplateOptions(entry.template || "common");
  refs.enabled.checked = Boolean(entry.enabled);
  refs.name.value = entry.name || "";
  refs.folderInput.value = entry.folder || "";
  refs.tags.value = (entry.tags || []).join(", ");
  refs.priority.value = entry.priority ?? 50;
  refs.probability.value = entry.probability ?? 1;
  refs.duration.value = entry.duration ?? 180;
  refs.times.value = entry.times ?? 5;
  refs.keywords.value = (entry.keywords || []).join("\n");
  refs.keywordMode.value = entry.keyword_mode || "modern";
  refs.cron.value = entry.cron || "";
  refs.scope.value = (entry.scope || []).join("\n");
  refs.content.value = entry.content || "";
}

function newEntry() {
  if (!state.templates.length) {
    showToast("条目模板尚未加载，请稍后重试", "warning");
    return;
  }
  state.editingId = null;
  state.editorSequence += 1;
  refs.editorTitle.textContent = "新建条目";
  refs.deleteEntry.hidden = true;
  refs.duplicateEntry.hidden = true;
  const template = templateFor(refs.templateFilter.value) || templateFor("common") || state.templates[0];
  setFormValues({ template: template.key, ...template.defaults, name: "", content: "", tags: [], scope: [] });
  setFormError();
  openDrawer();
}

async function openEditor(entryId) {
  const sequence = ++state.editorSequence;
  setFormError();
  refs.editorTitle.textContent = "读取条目…";
  refs.deleteEntry.hidden = true;
  refs.duplicateEntry.hidden = true;
  openDrawer();
  try {
    const result = await bridge.apiGet(`entry/${entryId}`);
    if (sequence !== state.editorSequence) return;
    state.revision = result.revision;
    state.templates = result.templates || state.templates;
    state.editingId = entryId;
    state.contentCache.set(entryId, result.entry.content || "");
    refs.editorTitle.textContent = `编辑：${result.entry.name}`;
    refs.deleteEntry.hidden = false;
    refs.duplicateEntry.hidden = false;
    setFormValues(result.entry);
  } catch (error) {
    if (sequence !== state.editorSequence) return;
    setFormError(asErrorMessage(error));
    refs.editorTitle.textContent = "无法打开条目";
  }
}

function applySelectedTemplate() {
  if (state.editingId) return;
  const template = templateFor(refs.template.value);
  if (!template) return;
  const defaults = template.defaults || {};
  refs.enabled.checked = defaults.enabled ?? true;
  refs.priority.value = defaults.priority ?? 50;
  refs.probability.value = defaults.probability ?? 1;
  refs.duration.value = defaults.duration ?? 180;
  refs.times.value = defaults.times ?? 5;
  refs.keywords.value = (defaults.keywords || []).join("\n");
  refs.cron.value = defaults.cron || "";
}

function formPayload() {
  const name = refs.name.value.trim();
  const content = refs.content.value;
  if (!name) throw new Error("请填写条目名称");
  if (!content.trim()) throw new Error("请填写注入内容");
  return {
    template: refs.template.value,
    enabled: refs.enabled.checked,
    name,
    folder: refs.folderInput.value.trim(),
    tags: splitList(refs.tags.value),
    priority: Number(refs.priority.value),
    probability: Number(refs.probability.value),
    duration: Number(refs.duration.value),
    times: Number(refs.times.value),
    keywords: splitList(refs.keywords.value),
    keyword_mode: refs.keywordMode.value,
    cron: refs.cron.value.trim(),
    scope: splitList(refs.scope.value),
    content,
  };
}

async function saveEntry(event) {
  event.preventDefault();
  setFormError();
  let entry;
  try {
    entry = formPayload();
  } catch (error) {
    setFormError(asErrorMessage(error));
    return;
  }
  updateBusy(refs.saveEntry, true, "保存中…");
  try {
    const endpoint = state.editingId ? `entry/${state.editingId}/save` : "entry/create";
    const result = await bridge.apiPost(endpoint, { revision: state.revision, entry });
    state.revision = result.revision;
    state.contentCache.set(result.entry.id, entry.content);
    showToast(`已保存“${result.entry.name}”`);
    closeEditor();
    state.selected.clear();
    await loadEntries();
  } catch (error) {
    const message = asErrorMessage(error);
    setFormError(message);
    if (isStale(message)) await loadEntries();
  } finally {
    updateBusy(refs.saveEntry, false);
  }
}

async function deleteEntry(entryId, entryName, button = null) {
  const confirmed = await requestConfirmation({
    title: "删除此条目？",
    message: `“${entryName}”将从世界树中永久移除。`,
    hint: "此操作无法在管理台内撤销；如需保留，请先导出备份。",
    confirmLabel: "永久删除",
  });
  if (!confirmed) return;
  updateBusy(button, true, "删除中…");
  try {
    const result = await bridge.apiPost(`entry/${entryId}/delete`, {
      revision: state.revision,
    });
    state.revision = result.revision;
    if (state.editingId === entryId) closeEditor();
    state.selected.delete(entryId);
    state.expanded.delete(entryId);
    state.contentCache.delete(entryId);
    showToast(`已删除“${result.deleted.name}”`);
    await loadEntries();
  } catch (error) {
    const message = asErrorMessage(error);
    if (state.editingId === entryId) setFormError(message);
    else showToast(message, "error");
    if (isStale(message)) await loadEntries();
  } finally {
    updateBusy(button, false);
  }
}

function deleteCurrentEntry() {
  if (!state.editingId) return;
  const entryName = refs.name.value.trim() || "此条目";
  return deleteEntry(state.editingId, entryName, refs.deleteEntry);
}

async function duplicateEntry(entryId, button = null) {
  updateBusy(button, true, "复制中…");
  try {
    const result = await bridge.apiPost(`entry/${entryId}/duplicate`, {
      revision: state.revision,
    });
    state.revision = result.revision;
    showToast(`已复制为“${result.entry.name}”`);
    state.selected.clear();
    await loadEntries();
    await openEditor(result.entry.id);
  } catch (error) {
    const message = asErrorMessage(error);
    if (state.editingId === entryId) setFormError(message);
    else showToast(message, "error");
    if (isStale(message)) await loadEntries();
  } finally {
    updateBusy(button, false);
  }
}

function duplicateCurrentEntry() {
  if (!state.editingId) return;
  return duplicateEntry(state.editingId, refs.duplicateEntry);
}

async function toggleEntry(entry, button) {
  updateBusy(button, true);
  try {
    const result = await bridge.apiPost(`entry/${entry.id}/toggle`, {
      revision: state.revision,
      enabled: !entry.enabled,
    });
    state.revision = result.revision;
    showToast(`已${result.entry.enabled ? "启用" : "停用"}“${result.entry.name}”`);
    // Grouping or filtering by enabled state means the card has to move; in every
    // other view we can update it in place and keep the reader's scroll position.
    const affectsLayout =
      state.filters.sort === "enabled" || ["enabled", "disabled"].includes(state.filters.status);
    if (affectsLayout) {
      await loadEntries();
      return;
    }
    entry.enabled = result.entry.enabled;
    entry.updated_at = result.entry.updated_at ?? entry.updated_at;
    const delta = entry.enabled ? 1 : -1;
    state.stats.enabled = Math.max(0, (state.stats.enabled ?? 0) + delta);
    state.stats.disabled = Math.max(0, (state.stats.disabled ?? 0) - delta);
    renderStats(state.stats);
    refreshCard(entry);
  } catch (error) {
    const message = asErrorMessage(error);
    showToast(message, "error");
    if (isStale(message)) await loadEntries();
  } finally {
    updateBusy(button, false);
  }
}

async function applyBulk() {
  const ids = [...state.selected];
  if (!ids.length) return;
  let action = refs.bulkAction.value;
  let value = refs.bulkValue.value.trim();
  if (action === "set_enabled_true") {
    action = "set_enabled";
    value = true;
  } else if (action === "set_enabled_false") {
    action = "set_enabled";
    value = false;
  } else if (VALUE_ACTIONS.includes(action) && !value) {
    showToast("请填写文件夹或标签", "warning");
    return;
  } else if (action === "delete") {
    const confirmed = await requestConfirmation({
      title: `删除 ${ids.length} 个条目？`,
      message: "所选枝叶将从世界树中永久移除。",
      hint: "此操作无法在管理台内撤销；如需保留，请先导出备份。",
      confirmLabel: `删除 ${ids.length} 项`,
    });
    if (!confirmed) return;
    value = null;
  }
  updateBusy(refs.bulkApply, true, "应用中…");
  try {
    const result = await bridge.apiPost("entries/bulk", {
      revision: state.revision,
      entry_ids: ids,
      action,
      value,
    });
    state.revision = result.revision;
    state.selected.clear();
    refs.bulkValue.value = "";
    showToast(`已更新 ${result.changed.length} 个条目`);
    await loadEntries();
  } catch (error) {
    const message = asErrorMessage(error);
    showToast(message, "error");
    if (isStale(message)) await loadEntries();
  } finally {
    updateBusy(refs.bulkApply, false);
  }
}

/* ------------------------------------------------------- import / export */

function openImportDialog() {
  setImportError();
  refs.importFile.value = "";
  syncImportFileName();
  refs.importForm.querySelector(".file-drop")?.classList.remove("is-dragging");
  refs.importDialog.showModal();
  // land on the file picker instead of the close button
  refs.importFile.focus({ preventScroll: true });
}

function syncImportFileName() {
  const file = refs.importFile.files?.[0];
  refs.importFileName.textContent = file
    ? `${file.name}（${Math.ceil(file.size / 1024)} KiB）`
    : "尚未选择文件";
}

function closeImportDialog() {
  if (refs.importDialog.open) refs.importDialog.close();
}

async function importEntries(event) {
  event.preventDefault();
  const file = refs.importFile.files?.[0];
  if (!file) {
    setImportError("请选择 JSON、YAML 或 YML 文件");
    return;
  }
  if (file.size > 2 * 1024 * 1024) {
    setImportError("导入文件不能超过 2 MiB");
    return;
  }
  setImportError();
  updateBusy(refs.confirmImport, true, "导入中…");
  try {
    const result = await bridge.upload(
      `import/${refs.importStrategy.value}/${state.revision}`,
      file,
    );
    state.revision = result.revision;
    const report = result.report;
    const summary = [
      `新增 ${report.added}`,
      report.replaced ? `覆盖 ${report.replaced}` : "",
      report.renamed ? `重命名 ${report.renamed}` : "",
      report.skipped ? `跳过 ${report.skipped}` : "",
      report.invalid ? `无效 ${report.invalid}` : "",
    ].filter(Boolean).join("，");
    closeImportDialog();
    showToast(`导入完成：${summary}`);
    if (report.messages?.length) showToast(report.messages[0], "warning");
    state.selected.clear();
    state.filters.page = 1;
    await loadEntries();
  } catch (error) {
    const message = asErrorMessage(error);
    setImportError(message);
    if (isStale(message)) await loadEntries();
  } finally {
    updateBusy(refs.confirmImport, false);
  }
}

async function exportEntries(format, button, ids = []) {
  updateBusy(button, true, "生成中…");
  const params = { format };
  if (ids.length) params.ids = ids.join(",");
  const extension = format === "json" ? "json" : "yaml";
  const stem = ids.length ? "worldtree-lore-selected" : "worldtree-lore";
  try {
    await bridge.download("export", params, `${stem}.${extension}`);
    showToast(ids.length ? `已开始下载 ${ids.length} 个条目` : `已开始下载 ${format.toUpperCase()} 备份`);
  } catch (error) {
    showToast(asErrorMessage(error), "error");
  } finally {
    updateBusy(button, false);
  }
}

/* --------------------------------------------------------------- filters */

function updateFilters({ resetPage = true, resetGroups = false } = {}) {
  state.filters.q = refs.search.value.trim();
  state.filters.status = refs.status.value;
  state.filters.template = refs.templateFilter.value;
  state.filters.folder = refs.folder.value;
  state.filters.tag = refs.tag.value;
  state.filters.sort = refs.sort.value;
  state.filters.page_size = Number(refs.pageSize.value);
  if (resetPage) state.filters.page = 1;
  if (resetGroups) state.collapsed.clear();
  state.selected.clear();
  loadEntries();
}

/* ---------------------------------------------------------------- events */

function bindEvents() {
  refs.refresh.addEventListener("click", () => loadEntries({ announce: true }));
  refs.newEntry.addEventListener("click", newEntry);
  refs.emptyCreate.addEventListener("click", newEntry);
  refs.selectPage.addEventListener("click", toggleCurrentPageSelection);
  refs.density.addEventListener("click", toggleDensity);
  refs.collapseAll.addEventListener("click", toggleAllBranches);
  refs.mobileFilterToggle.addEventListener("click", toggleMobileFilters);
  refs.clearFilters.addEventListener("click", () => {
    refs.search.value = "";
    refs.status.value = "all";
    refs.templateFilter.value = "";
    refs.folder.value = "";
    refs.tag.value = "";
    updateFilters({ resetGroups: true });
  });
  refs.search.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => updateFilters(), 260);
  });
  for (const control of [refs.status, refs.templateFilter, refs.folder, refs.tag]) {
    control.addEventListener("change", () => updateFilters());
  }
  refs.sort.addEventListener("change", () => {
    writePref("sort", refs.sort.value);
    updateFilters({ resetGroups: true });
  });
  refs.pageSize.addEventListener("change", () => {
    writePref("pageSize", refs.pageSize.value);
    updateFilters();
  });
  refs.bulkAction.addEventListener("change", updateBulkValueState);
  refs.bulkApply.addEventListener("click", applyBulk);
  refs.exportSelected.addEventListener("click", () =>
    exportEntries("yaml", refs.exportSelected, [...state.selected]),
  );
  refs.clearSelection.addEventListener("click", () => {
    state.selected.clear();
    renderEntries();
  });
  refs.closeEditor.addEventListener("click", closeEditor);
  refs.cancelEditor.addEventListener("click", closeEditor);
  refs.deleteEntry.addEventListener("click", deleteCurrentEntry);
  refs.duplicateEntry.addEventListener("click", duplicateCurrentEntry);
  refs.drawerScrim.addEventListener("click", closeEditor);
  refs.entryForm.addEventListener("submit", saveEntry);
  refs.entryForm.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      refs.entryForm.requestSubmit();
    }
  });
  refs.template.addEventListener("change", applySelectedTemplate);
  refs.importButton.addEventListener("click", openImportDialog);
  refs.closeImport.addEventListener("click", closeImportDialog);
  refs.cancelImport.addEventListener("click", closeImportDialog);
  refs.importForm.addEventListener("submit", importEntries);
  refs.cancelConfirm.addEventListener("click", () => settleConfirmation(false));
  refs.acceptConfirm.addEventListener("click", () => settleConfirmation(true));
  refs.confirmDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    settleConfirmation(false);
  });
  refs.confirmDialog.addEventListener("click", (event) => {
    if (event.target === refs.confirmDialog) settleConfirmation(false);
  });
  refs.importFile.addEventListener("change", syncImportFileName);
  const dropZone = refs.importForm.querySelector(".file-drop");
  if (dropZone) {
    const setDragging = (on) => dropZone.classList.toggle("is-dragging", on);
    dropZone.addEventListener("dragover", (event) => {
      event.preventDefault();
      setDragging(true);
    });
    dropZone.addEventListener("dragleave", () => setDragging(false));
    dropZone.addEventListener("drop", (event) => {
      event.preventDefault();
      setDragging(false);
      const dropped = event.dataTransfer?.files?.[0];
      if (!dropped) return;
      if (!/\.(json|ya?ml)$/i.test(dropped.name)) {
        setImportError("只支持 JSON、YAML 或 YML 文件");
        return;
      }
      const bucket = new DataTransfer();
      bucket.items.add(dropped);
      refs.importFile.files = bucket.files;
      setImportError();
      syncImportFileName();
    });
  }
  refs.exportYaml.addEventListener("click", () => exportEntries("yaml", refs.exportYaml));
  refs.exportJson.addEventListener("click", () => exportEntries("json", refs.exportJson));

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element) || !event.target.closest(".entry-menu")) closeAllMenus();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (refs.confirmDialog.open) {
        event.preventDefault();
        settleConfirmation(false);
        return;
      }
      if (refs.importDialog.open) return;
      if (refs.list.querySelector(".entry-menu.is-open")) {
        closeAllMenus();
        return;
      }
      if (refs.drawer.getAttribute("aria-hidden") === "false") closeEditor();
      return;
    }
    if (refs.drawer.getAttribute("aria-hidden") === "false") return;
    if (refs.confirmDialog.open || refs.importDialog.open) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (isTypingTarget(event.target)) return;
    if (event.key === "/") {
      event.preventDefault();
      refs.search.focus();
      refs.search.select();
      return;
    }
    if (event.key === "n" || event.key === "N") {
      event.preventDefault();
      newEntry();
    }
  });
}

/* ------------------------------------------------------------------- boot */

function applyPageContext(context = {}) {
  const current = context?.isDark === undefined && context?.theme === undefined
    ? (bridge.getContext?.() || context || {})
    : context;
  document.documentElement.lang = bridge.getLocale?.() || current.locale || "zh-CN";
  if (typeof current.isDark === "boolean" || current.theme) {
    const isDark = current.isDark === true || current.theme === "dark";
    document.documentElement.dataset.theme = isDark ? "dark" : "light";
  } else if (!document.documentElement.dataset.theme) {
    document.documentElement.dataset.theme = "light";
  }
}

function restorePreferences() {
  const sort = readPref("sort", DEFAULT_SORT);
  if (Object.hasOwn(SORT_LABELS, sort)) {
    refs.sort.value = sort;
    state.filters.sort = sort;
  }
  const pageSize = readPref("pageSize", String(DEFAULT_PAGE_SIZE));
  if ([...refs.pageSize.options].some((option) => option.value === pageSize)) {
    refs.pageSize.value = pageSize;
    state.filters.page_size = Number(pageSize);
  }
  state.density = readPref("density", "cosy") === "compact" ? "compact" : "cosy";
  applyDensity();
}

async function initialise() {
  if (!bridge) {
    setConnection("error", "Plugin Page bridge 不可用");
    showToast("AstrBot 未注入插件页面桥接；请升级到 AstrBot 4.24.2 或更高版本。", "error");
    return;
  }
  restorePreferences();
  bindEvents();
  try {
    const context = await bridge.ready();
    applyPageContext(context || {});
    document.title = bridge.t?.("pages.worldtree.title", "世界树管理台") || "世界树管理台";
    $("#pageTitle").textContent = document.title;
    await loadEntries();
    await loadDiagnostics();
    bridge.onContext?.((nextContext) => applyPageContext(nextContext || {}));
  } catch (error) {
    setConnection("error", "初始化失败");
    showToast(asErrorMessage(error), "error");
  }
}

initialise();
