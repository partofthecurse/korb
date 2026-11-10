const themeButton = document.querySelector('.theme-toggle');
const storedTheme = localStorage.getItem('korb-theme');
document.documentElement.dataset.theme = storedTheme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
themeButton?.addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('korb-theme', next);
});
document.querySelectorAll('form[data-confirm]').forEach(form => {
  form.addEventListener('submit', event => {
    if (!confirm(form.dataset.confirm)) event.preventDefault();
  });
});

const layoutOptions = ['2', '3', '4', 'list'];
const storedLayout = localStorage.getItem('korb-tile-layout');
let tileLayout = layoutOptions.includes(storedLayout) ? storedLayout : '4';

function setTileLayout(layout) {
  if (!layoutOptions.includes(layout)) return;
  tileLayout = layout;
  document.body.dataset.tileLayout = layout;
  localStorage.setItem('korb-tile-layout', layout);
  document.querySelectorAll('.view-segments [data-layout]').forEach(button => {
    const selected = button.dataset.layout === layout;
    button.setAttribute('aria-pressed', String(selected));
    button.classList.toggle('selected', selected);
  });
}

document.querySelectorAll('.view-segments [data-layout]').forEach(button => {
  button.addEventListener('click', () => setTileLayout(button.dataset.layout));
});
setTileLayout(tileLayout);

let categoryOrder = [];

function setupFilter(input, items, text) {
  input?.addEventListener('input', () => {
    const query = input.value.trim().toLocaleLowerCase('de');
    items.forEach(item => { item.hidden = Boolean(query) && !text(item).includes(query); });
  });
}

const foodRows = [...document.querySelectorAll('.food-row')];
setupFilter(document.querySelector('.food-filter'), foodRows, row => row.dataset.search);
document.querySelector('.food-filter')?.addEventListener('input', () => {
  document.querySelectorAll('.food-group').forEach(group => {
    group.hidden = ![...group.querySelectorAll('.food-row')].some(row => !row.hidden);
  });
});
setupFilter(document.querySelector('.icon-filter'), [...document.querySelectorAll('.icon-option')],
  option => option.title.toLocaleLowerCase('de'));
document.querySelector('.icon-option input:checked')?.closest('.icon-option')?.scrollIntoView({block: 'nearest'});

const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf, ...options.headers}
  });
  if (response.status === 401) { location.href = '/login'; throw new Error('Anmeldung erforderlich'); }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || 'Die Aktion konnte nicht ausgeführt werden.');
  }
  return response.status === 204 ? null : response.json();
}

const activeItems = document.querySelector('#active-items');
if (activeItems) {
  const completedItems = document.querySelector('#completed-items');
  const status = document.querySelector('#sync-status');
  const statusDot = document.querySelector('#status-dot');
  const amazonConfigured = document.querySelector('.sync-line')?.dataset.amazonConfigured === 'true';
  let loading = false;

  function tile(item) {
    const element = document.createElement('article');
    element.className = `tile ${item.color}${item.completed ? ' is-completed' : ''}`;
    const remove = document.createElement('button');
    remove.className = 'tile-remove';
    remove.type = 'button';
    remove.title = 'Artikel entfernen';
    remove.setAttribute('aria-label', `${item.name} entfernen`);
    remove.textContent = '×';
    remove.addEventListener('click', async () => {
      try { await api(`/api/items/${item.id}`, {method: 'DELETE'}); await refresh(); }
      catch (error) { status.textContent = error.message; }
    });
    const toggle = document.createElement('button');
    toggle.className = 'tile-main';
    toggle.type = 'button';
    toggle.title = item.completed ? 'Wieder auf die Liste' : 'Als erledigt markieren';
    toggle.setAttribute('aria-label', `${item.name}: ${toggle.title}`);
    const iconElement = document.createElement('img');
    iconElement.className = 'food-icon';
    iconElement.src = `/static/icons/${item.icon}.svg`;
    iconElement.alt = '';
    const title = document.createElement('span');
    title.className = 'tile-name';
    title.textContent = item.name;
    title.title = item.name;
    const check = document.createElement('span');
    check.className = 'tile-check';
    check.textContent = item.completed ? '✓' : '+';
    toggle.append(iconElement, title, check);
    toggle.addEventListener('click', async () => {
      try { await api(`/api/items/${item.id}`, {method: 'PATCH', body: JSON.stringify({completed: !item.completed})}); await refresh(); }
      catch (error) { status.textContent = error.message; }
    });

    const stepper = document.createElement('div');
    stepper.className = 'quantity-stepper';
    const decrement = document.createElement('button');
    decrement.className = 'quantity-control';
    decrement.type = 'button';
    decrement.textContent = '−';
    decrement.title = 'Menge verringern';
    decrement.setAttribute('aria-label', `${item.name} Menge verringern`);
    decrement.disabled = item.quantity <= 1;
    const quantity = document.createElement('span');
    quantity.className = 'quantity-value';
    quantity.setAttribute('aria-label', `Menge ${item.quantity}`);
    quantity.textContent = item.quantity;
    const increment = document.createElement('button');
    increment.className = 'quantity-control';
    increment.type = 'button';
    increment.textContent = '+';
    increment.title = 'Menge erhöhen';
    increment.setAttribute('aria-label', `${item.name} Menge erhöhen`);
    increment.disabled = item.quantity >= 99;
    for (const [button, delta] of [[decrement, -1], [increment, 1]]) {
      button.addEventListener('click', async () => {
        try { await api(`/api/items/${item.id}`, {method: 'PATCH', body: JSON.stringify({quantity_delta: delta})}); await refresh(); }
        catch (error) { status.textContent = error.message; }
      });
    }
    stepper.append(decrement, quantity, increment);
    element.append(remove, toggle, stepper);
    return element;
  }

  function renderGroups(container, items) {
    const groups = new Map(categoryOrder.map(category => [category, []]));
    for (const item of items) (groups.get(item.category) || groups.get('Sonstiges')).push(item);
    const sections = [];
    for (const category of categoryOrder) {
      const entries = groups.get(category);
      if (!entries.length) continue;
      entries.sort((left, right) => left.name.localeCompare(right.name, 'de'));
      const section = document.createElement('section');
      section.className = 'category-group';
      const heading = document.createElement('div');
      heading.className = 'category-heading';
      const title = document.createElement('h3');
      title.textContent = category;
      const count = document.createElement('span');
      count.className = 'count-badge';
      count.textContent = entries.length;
      heading.append(title, count);
      const grid = document.createElement('div');
      grid.className = 'tile-grid';
      grid.append(...entries.map(tile));
      section.append(heading, grid);
      sections.push(section);
    }
    container.replaceChildren(...sections);
  }

  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const data = await api('/api/items');
      categoryOrder = data.categories;
      const active = data.items.filter(item => !item.completed);
      const completed = data.items.filter(item => item.completed);
      renderGroups(activeItems, active);
      renderGroups(completedItems, completed);
      document.querySelector('#item-count').textContent = active.reduce((total, item) => total + item.quantity, 0);
      document.querySelector('#active-count').textContent = active.length;
      document.querySelector('#completed-count').textContent = completed.length;
      document.querySelector('#empty-state').hidden = active.length !== 0;
      document.querySelector('#completed-section').hidden = completed.length === 0;
      statusDot.classList.toggle('error', Boolean(data.sync.error));
      status.textContent = !amazonConfigured ? 'Amazon-Synchronisierung nicht eingerichtet' : data.sync.running ? 'Synchronisierung läuft …' : data.sync.error || (data.sync.last_success ? `Zuletzt synchronisiert: ${new Date(data.sync.last_success).toLocaleString('de-DE')}` : 'Noch nicht synchronisiert');
    } catch (error) { status.textContent = error.message; statusDot.classList.add('error'); }
    finally { loading = false; }
  }
  document.querySelector('#add-form').addEventListener('submit', async event => {
    event.preventDefault();
    const input = document.querySelector('#item-name');
    try { await api('/api/items', {method: 'POST', body: JSON.stringify({name: input.value})}); input.value = ''; await refresh(); input.focus(); }
    catch (error) { status.textContent = error.message; }
  });
  const mobileDialog = document.querySelector('#mobile-add-dialog');
  const mobileInput = document.querySelector('#mobile-item-name');
  document.querySelector('#mobile-add-button').addEventListener('click', () => mobileDialog.showModal());
  document.querySelector('#mobile-add-close').addEventListener('click', () => mobileDialog.close());
  mobileDialog.addEventListener('click', event => {
    if (event.target === mobileDialog) mobileDialog.close();
  });
  document.querySelector('#mobile-add-form').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      await api('/api/items', {method: 'POST', body: JSON.stringify({name: mobileInput.value})});
      mobileInput.value = '';
      mobileDialog.close();
      await refresh();
    } catch (error) { status.textContent = error.message; }
  });
  document.querySelector('#sync-button')?.addEventListener('click', async () => {
    try { await api('/api/sync', {method: 'POST', body: '{}'}); await refresh(); }
    catch (error) { status.textContent = error.message; }
  });
  refresh();
  setInterval(refresh, 10000);
}