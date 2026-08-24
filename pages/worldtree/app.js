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
  folder: $("#folderFilter"),
  tag: $("#tagFilter"),
  clearFilters: $("#clearFiltersButton"),
  pageSize: $("#pageSizeSelect"),
  selectPage: $("#selectPageButton"),
  stats: $("#statsGrid"),
  resultTitle: $("#resultTitle"),
  resultCaption: $("#resultCaption"),
  list: $("#entryList"),
  empty: $("#emptyState"),
  pagination: $("#pagination"),
  bulkBar: $("#bulkBar"),
  selectedCount: $("#selectedCount"),
  bulkAction: $("#bulkAction"),
  bulkValue: $("#bulkValue"),
  bulkApply: $("#bulkApplyButton"),
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
  toastRegion: $("#toastRegion"),
};

let lastFocusedElement = null;

const state = {
  revision: 0,
  templates: [],
  entries: [],
  selected: new Set(),
  editingId: null,
  editorSequence: 0,
  loading: false,
  loadSequence: 0,
  filters: {
    q: "",
    status: "all",
    folder: "",
    tag: "",
    page: 1,
    page_size: 30,
  },
  pagination: { page: 1, page_size: 30, total: 0, total_pages: 1 },
};

function create(tag, className = "", text = undefined) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
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

function updateBusy(button, busy, busyText = "处理中…") {
  if (!button) return;
  if (busy) {
    if (!button.disabled) button.dataset.originalText = button.textContent;
    button.textContent = busyText;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
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

function setFormError(message = "") {
  refs.formError.hidden = !message;
  refs.formError.textContent = message;
}

function setImportError(message = "") {
  refs.importError.hidden = !message;
  refs.importError.textContent = message;
}

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

function syncFilterControls(facets) {
  setSelectOptions(refs.folder, "全部文件夹", facets.folders || [], state.filters.folder);
  setSelectOptions(refs.tag, "全部标签", facets.tags || [], state.filters.tag);
  state.filters.folder = refs.folder.value;
  state.filters.tag = refs.tag.value;
}

function renderStats(stats) {
  const items = [
    [stats.total, "全部"],
    [stats.enabled, "启用"],
    [stats.folders, "文件夹"],
    [stats.scheduled, "日程"],
  ];
  refs.stats.replaceChildren();
  for (const [value, label] of items) {
    const cell = create("div", "stat");
    cell.append(create("strong", "", String(value ?? 0)), create("span", "", label));
    refs.stats.append(cell);
  }
}

function badge(text, className) {
  return create("span", className, text);
}

function cardFor(entry) {
  const card = create("article", "entry-card");
  card.dataset.entryId = entry.id;
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
  main.tabIndex = 0;
  main.setAttribute("role", "button");
  main.setAttribute("aria-label", `编辑条目 ${entry.name}`);
  main.addEventListener("click", () => openEditor(entry.id));
  main.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openEditor(entry.id);
    }
  });

  const title = create("div", "entry-title");
  title.append(create("h3", "", entry.name));
  title.append(
    badge(entry.enabled ? "启用" : "禁用", `status-badge ${entry.enabled ? "enabled" : "disabled"}`),
  );
  if (entry.folder) title.append(badge(entry.folder, "folder-pill"));
  title.append(badge(`优先级 ${entry.priority}`, "trigger-pill"));
  if (entry.cron) title.append(badge("Cron", "trigger-pill"));

  const meta = create("div", "entry-meta");
  for (const tag of entry.tags || []) meta.append(badge(`#${tag}`, "tag"));
  const keywordCount = (entry.keywords || []).length;
  if (keywordCount) meta.append(badge(`${keywordCount} 个触发器`, "trigger-pill"));
  if ((entry.scope || []).length) meta.append(badge("范围限定", "trigger-pill"));

  main.append(title, meta, create("p", "entry-preview", entry.preview || "（无内容预览）"));

  const actions = create("div", "entry-actions");
  const toggle = create("button", `toggle ${entry.enabled ? "on" : ""}`);
  toggle.type = "button";
  toggle.title = entry.enabled ? "关闭条目" : "开启条目";
  toggle.setAttribute("aria-label", toggle.title);
  toggle.setAttribute("aria-pressed", String(entry.enabled));
  toggle.addEventListener("click", async (event) => {
    event.stopPropagation();
    await toggleEntry(entry, toggle);
  });
  const edit = create("button", "button button-ghost", "编辑");
  edit.type = "button";
  edit.addEventListener("click", () => openEditor(entry.id));
  actions.append(toggle, edit);

  card.append(checkbox, main, actions);
  return card;
}

function renderEntries() {
  refs.list.replaceChildren();
  const entries = state.entries;
  refs.empty.hidden = entries.length !== 0;
  refs.list.hidden = entries.length === 0;
  for (const entry of entries) refs.list.append(cardFor(entry));
  const total = state.pagination.total || 0;
  refs.resultTitle.textContent = total ? `共 ${total} 个条目` : "没有匹配的条目";
  refs.resultCaption.textContent = state.filters.q
    ? `搜索：${state.filters.q}`
    : "条目列表";
  renderPagination();
  renderBulkBar();
  renderSelectPageButton();
}

function pageButton(label, page, { current = false, disabled = false } = {}) {
  const button = create("button", `page-button ${current ? "current" : ""}`, label);
  button.type = "button";
  button.disabled = disabled;
  button.setAttribute("aria-current", current ? "page" : "false");
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
  if (totalPages <= 1) return;
  refs.pagination.append(pageButton("‹", Math.max(1, page - 1), { disabled: page === 1 }));
  const pages = new Set([1, totalPages, page - 1, page, page + 1]);
  const ordered = [...pages].filter((item) => item >= 1 && item <= totalPages).sort((a, b) => a - b);
  let previous = 0;
  for (const item of ordered) {
    if (item - previous > 1) refs.pagination.append(create("span", "", "…"));
    refs.pagination.append(pageButton(String(item), item, { current: item === page }));
    previous = item;
  }
  refs.pagination.append(pageButton("›", Math.min(totalPages, page + 1), { disabled: page === totalPages }));
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
  const needsValue = ["set_folder", "add_tag", "remove_tag"].includes(refs.bulkAction.value);
  refs.bulkValue.disabled = !needsValue;
  refs.bulkValue.placeholder = needsValue ? "文件夹或标签" : "此操作无需值";
}

async function loadEntries({ announce = false } = {}) {
  if (!bridge) return;
  const sequence = ++state.loadSequence;
  state.loading = true;
  updateBusy(refs.refresh, true, "读取中…");
  try {
    const result = await bridge.apiGet("entries", state.filters);
    if (sequence !== state.loadSequence) return;
    state.revision = result.revision;
    state.entries = result.entries || [];
    state.pagination = result.pagination || state.pagination;
    state.templates = result.templates || state.templates;
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
      updateBusy(refs.refresh, false);
    }
  }
}

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
  const template = templateFor("common") || state.templates[0];
  setFormValues({ template: template.key, ...template.defaults, name: "", content: "", tags: [], scope: [] });
  setFormError();
  openDrawer();
}

async function openEditor(entryId) {
  const sequence = ++state.editorSequence;
  setFormError();
  refs.editorTitle.textContent = "读取条目…";
  refs.deleteEntry.hidden = true;
  openDrawer();
  try {
    const result = await bridge.apiGet(`entry/${entryId}`);
    if (sequence !== state.editorSequence) return;
    state.revision = result.revision;
    state.templates = result.templates || state.templates;
    state.editingId = entryId;
    refs.editorTitle.textContent = `编辑：${result.entry.name}`;
    refs.deleteEntry.hidden = false;
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
    showToast(`已保存“${result.entry.name}”`);
    closeEditor();
    state.selected.clear();
    await loadEntries();
  } catch (error) {
    const message = asErrorMessage(error);
    setFormError(message);
    if (message.includes("已被其他操作更新")) await loadEntries();
  } finally {
    updateBusy(refs.saveEntry, false);
  }
}

async function deleteCurrentEntry() {
  if (!state.editingId) return;
  const entryId = state.editingId;
  const entryName = refs.name.value.trim() || "此条目";
  if (!window.confirm(`确定删除“${entryName}”吗？此操作无法在管理台内撤销。`)) return;
  updateBusy(refs.deleteEntry, true, "删除中…");
  try {
    const result = await bridge.apiPost(`entry/${entryId}/delete`, {
      revision: state.revision,
    });
    state.revision = result.revision;
    closeEditor();
    state.selected.delete(entryId);
    showToast(`已删除“${result.deleted.name}”`);
    await loadEntries();
  } catch (error) {
    const message = asErrorMessage(error);
    setFormError(message);
    if (message.includes("已被其他操作更新")) await loadEntries();
  } finally {
    updateBusy(refs.deleteEntry, false);
  }
}

async function toggleEntry(entry, button) {
  updateBusy(button, true, "…");
  try {
    const result = await bridge.apiPost(`entry/${entry.id}/toggle`, {
      revision: state.revision,
      enabled: !entry.enabled,
    });
    state.revision = result.revision;
    showToast(`已${result.entry.enabled ? "启用" : "禁用"}“${result.entry.name}”`);
    await loadEntries();
  } catch (error) {
    const message = asErrorMessage(error);
    showToast(message, "error");
    if (message.includes("已被其他操作更新")) await loadEntries();
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
  } else if (["set_folder", "add_tag", "remove_tag"].includes(action) && !value) {
    showToast("请填写文件夹或标签", "warning");
    return;
  } else if (action === "delete") {
    if (!window.confirm(`确定要删除所选的 ${ids.length} 个条目吗？此操作无法在管理台内撤销。`)) return;
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
    if (message.includes("已被其他操作更新")) await loadEntries();
  } finally {
    updateBusy(refs.bulkApply, false);
  }
}

function openImportDialog() {
  setImportError();
  refs.importFile.value = "";
  refs.importFileName.textContent = "尚未选择文件";
  refs.importDialog.showModal();
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
    if (message.includes("已被其他操作更新")) await loadEntries();
  } finally {
    updateBusy(refs.confirmImport, false);
  }
}

async function exportEntries(format, button) {
  updateBusy(button, true, "生成中…");
  try {
    await bridge.download("export", { format }, `worldtree-lore.${format === "json" ? "json" : "yaml"}`);
    showToast(`已开始下载 ${format.toUpperCase()} 备份`);
  } catch (error) {
    showToast(asErrorMessage(error), "error");
  } finally {
    updateBusy(button, false);
  }
}

let searchTimer = null;
function updateFilters({ resetPage = true } = {}) {
  state.filters.q = refs.search.value.trim();
  state.filters.status = refs.status.value;
  state.filters.folder = refs.folder.value;
  state.filters.tag = refs.tag.value;
  state.filters.page_size = Number(refs.pageSize.value);
  if (resetPage) state.filters.page = 1;
  state.selected.clear();
  loadEntries();
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

function bindEvents() {
  refs.refresh.addEventListener("click", () => loadEntries({ announce: true }));
  refs.newEntry.addEventListener("click", newEntry);
  refs.emptyCreate.addEventListener("click", newEntry);
  refs.selectPage.addEventListener("click", toggleCurrentPageSelection);
  refs.mobileFilterToggle.addEventListener("click", toggleMobileFilters);
  refs.clearFilters.addEventListener("click", () => {
    refs.search.value = "";
    refs.status.value = "all";
    refs.folder.value = "";
    refs.tag.value = "";
    updateFilters();
  });
  refs.search.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => updateFilters(), 260);
  });
  for (const control of [refs.status, refs.folder, refs.tag, refs.pageSize]) {
    control.addEventListener("change", () => updateFilters());
  }
  refs.bulkAction.addEventListener("change", updateBulkValueState);
  refs.bulkApply.addEventListener("click", applyBulk);
  refs.clearSelection.addEventListener("click", () => {
    state.selected.clear();
    renderEntries();
  });
  refs.closeEditor.addEventListener("click", closeEditor);
  refs.cancelEditor.addEventListener("click", closeEditor);
  refs.deleteEntry.addEventListener("click", deleteCurrentEntry);
  refs.drawerScrim.addEventListener("click", closeEditor);
  refs.entryForm.addEventListener("submit", saveEntry);
  refs.template.addEventListener("change", applySelectedTemplate);
  refs.importButton.addEventListener("click", openImportDialog);
  refs.closeImport.addEventListener("click", closeImportDialog);
  refs.cancelImport.addEventListener("click", closeImportDialog);
  refs.importForm.addEventListener("submit", importEntries);
  refs.importFile.addEventListener("change", () => {
    const file = refs.importFile.files?.[0];
    refs.importFileName.textContent = file ? `${file.name}（${Math.ceil(file.size / 1024)} KiB）` : "尚未选择文件";
  });
  refs.exportYaml.addEventListener("click", () => exportEntries("yaml", refs.exportYaml));
  refs.exportJson.addEventListener("click", () => exportEntries("json", refs.exportJson));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && refs.drawer.getAttribute("aria-hidden") === "false") closeEditor();
  });
}

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

async function initialise() {
  if (!bridge) {
    setConnection("error", "Plugin Page bridge 不可用");
    showToast("AstrBot 未注入插件页面桥接；请升级到 AstrBot 4.24.2 或更高版本。", "error");
    return;
  }
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
