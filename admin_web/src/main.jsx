import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import './status.css'
import './promos.css'

const money = (minor, currency = 'RUB') => new Intl.NumberFormat('ru-RU', { style: 'currency', currency, maximumFractionDigits: 2 }).format((minor || 0) / 100)
const date = value => value ? new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'
const customerName = customer => customer?.first_name || customer?.username || (customer?.telegram_id ? `ID ${customer.telegram_id}` : 'Удалённый пользователь')

async function api(path, options = {}, token) {
  let response
  try {
    response = await fetch(`/api${path}`, { ...options, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers } })
  } catch {
    throw new Error('Нет соединения с сервером. Проверьте, что контейнеры запущены.')
  }
  const content = await response.json().catch(() => ({}))
  if (response.status === 401) window.dispatchEvent(new Event('dkmarket-session-expired'))
  if (!response.ok) throw new Error(content.detail || 'Не удалось выполнить запрос')
  return content
}

function Setup() {
  return <main className="auth-page"><section className="login-card setup"><span className="eyebrow">DKMARKET</span><h1>Настройте доступ</h1><p>Панель запущена, но учётная запись администратора ещё не создана.</p><ol><li>Сгенерируйте парольный хэш: <code>docker compose run --rm admin-api python -m admin_api.password</code></li><li>Добавьте вывод команды в <code>ADMIN_PASSWORD_HASH</code> и создайте секрет минимум из 32 символов в <code>ADMIN_JWT_SECRET</code> в файле <code>.env</code>.</li><li>Перезапустите: <code>docker compose up -d admin-api</code>.</li></ol><p className="subtle">Пароль остаётся только у вас: в проект сохраняется его Argon2-хэш.</p></section></main>
}

function Login({ onLogin }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async event => {
    event.preventDefault(); setBusy(true); setError('')
    try { const data = await api('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }); localStorage.setItem('dkmarket-admin-token', data.access_token); onLogin(data.access_token) }
    catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }
  return <main className="auth-page"><form className="login-card" onSubmit={submit}><span className="eyebrow">DKMARKET</span><h1>Администрирование</h1><p>Войдите, чтобы управлять заказами и поддержкой.</p><label>Логин<input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required /></label><label>Пароль<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>{error && <p className="error">{error}</p>}<button className="primary" disabled={busy}>{busy ? 'Входим…' : 'Войти'}</button></form></main>
}

function Stat({ label, value, accent }) { return <article className={`stat ${accent || ''}`}><span>{label}</span><strong>{value}</strong></article> }
function Badge({ children, type }) { return <span className={`badge ${type || ''}`}>{children}</span> }
function Loading({ text = 'Загружаем данные…', full = false }) { return <div className={full ? 'page-loading full' : 'page-loading'}><span className="spinner" />{text}</div> }

function Overview({ data }) { return <section><div className="page-title"><div><h2>Обзор</h2><p>Текущее состояние магазина</p></div></div><div className="stats"><Stat label="Пользователи" value={data?.users ?? '—'} /><Stat label="Заказы" value={data?.orders ?? '—'} /><Stat label="Открытые обращения" value={data?.open_tickets ?? '—'} accent="warm" /><Stat label="Товары в каталоге" value={data?.active_products ?? '—'} /></div></section> }

function Users({ data }) { return <section><div className="page-title"><div><h2>Пользователи</h2><p>{data?.total ?? 0} всего</p></div></div><div className="data-table users-table"><div className="thead"><span>Пользователь</span><span>Telegram ID</span><span>Дата регистрации</span></div>{data?.items?.length ? data.items.map(item => <div className="trow" key={item.id}><span><b>{customerName(item)}</b>{item.username && <small>@{item.username}</small>}</span><span>{item.telegram_id}</span><span>{date(item.created_at)}</span></div>) : <Empty text="Пользователей пока нет." />}</div></section> }

function Orders({ data }) { const [opened, setOpened] = useState(null); return <section><div className="page-title"><div><h2>Заказы</h2><p>{data?.total ?? 0} всего</p></div></div><div className="data-table orders-table"><div className="thead"><span>Номер</span><span>Клиент</span><span>Сумма</span><span>Статус</span><span>Дата</span></div>{data?.items?.length ? data.items.map(item => <button className="trow order-row" key={item.id} onClick={() => setOpened(item)}><span><b>{item.public_number}</b><small>{item.items.length} поз.</small></span><span>{customerName(item.customer)}</span><span>{money(item.total_minor, item.currency)}</span><span><Badge type={item.status}>{item.status === 'pending_payment' ? 'Ожидает оплаты' : item.status}</Badge></span><span>{date(item.created_at)}</span></button>) : <Empty text="Заказов пока нет." />}</div>{opened && <div className="modal-backdrop" onClick={() => setOpened(null)}><article className="modal" onClick={e => e.stopPropagation()}><button className="icon-button close" onClick={() => setOpened(null)}>×</button><h3>{opened.public_number}</h3><p>{customerName(opened.customer)} · {date(opened.created_at)}</p><div className="line-items">{opened.items.map((line, index) => <div key={index}><span>{line.name} × {line.quantity}</span><b>{money(line.total_minor, opened.currency)}</b></div>)}</div><strong className="order-total">Итого: {money(opened.total_minor, opened.currency)}</strong></article></div>}</section> }

const PRODUCT_TYPES = [['', 'Все разделы'], ['telegram', 'Telegram'], ['game_topup', 'Пополнение игр'], ['gift_card', 'Подарочные карты'], ['steam_gift', 'Steam'], ['game_key', 'Ключи']]

function PriceCell({ item, token, onSaved, onError }) {
  const [editing, setEditing] = useState(false); const [value, setValue] = useState(''); const [busy, setBusy] = useState(false)
  const start = () => { setValue(String((item.price_minor || 0) / 100)); setEditing(true) }
  const save = async event => { event.preventDefault(); setBusy(true); try { await api(`/products/${item.id}/price`, { method: 'POST', body: JSON.stringify({ price_rub: value.replace(',', '.') }) }, token); setEditing(false); await onSaved() } catch (err) { onError(err.message) } finally { setBusy(false) } }
  const supplier = item.supplier_price_usd ? `поставщик $${Number(item.supplier_price_usd).toFixed(2)}` : null
  if (editing) return <form className="price-edit" onSubmit={save}><input autoFocus value={value} onChange={e => setValue(e.target.value)} inputMode="decimal" /><button className="secondary" disabled={busy}>{busy ? '…' : 'OK'}</button><button type="button" className="secondary" onClick={() => setEditing(false)}>×</button></form>
  return <span><button className="price-button" onClick={start} title="Изменить цену">{money(item.price_minor, item.currency)} ✎</button>{(supplier || item.price_locked) && <small>{supplier}{item.price_locked ? ' · 🔒 вручную' : ''}</small>}</span>
}

function Reprice({ token, onDone }) {
  const [form, setForm] = useState({ product_type: '', rub_per_usd: '95', markup_percent: '15' }); const [result, setResult] = useState(''); const [busy, setBusy] = useState(false)
  const set = key => event => setForm({ ...form, [key]: event.target.value })
  const submit = async event => {
    event.preventDefault()
    const section = PRODUCT_TYPES.find(([value]) => value === form.product_type)[1]
    if (!window.confirm(`Пересчитать цены (${section}): цена поставщика × ${form.rub_per_usd} ₽ + ${form.markup_percent}%? Цены, изменённые вручную (🔒), не трогаются.`)) return
    setBusy(true)
    try { const data = await api('/products/reprice', { method: 'POST', body: JSON.stringify({ ...form, rub_per_usd: form.rub_per_usd.replace(',', '.'), markup_percent: form.markup_percent.replace(',', '.') }) }, token); setResult(`Обновлено товаров: ${data.updated}`); await onDone() } catch (err) { setResult(err.message) } finally { setBusy(false) }
  }
  return <form className="reprice" onSubmit={submit}><b>Массовая наценка</b><select value={form.product_type} onChange={set('product_type')}>{PRODUCT_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><label>Курс ₽/$<input value={form.rub_per_usd} onChange={set('rub_per_usd')} inputMode="decimal" /></label><label>Наценка %<input value={form.markup_percent} onChange={set('markup_percent')} inputMode="decimal" /></label><button className="secondary" disabled={busy}>{busy ? 'Считаем…' : 'Пересчитать'}</button>{result && <small>{result}</small>}</form>
}

function Catalog({ token, refreshKey }) {
  const [items, setItems] = useState([]); const [query, setQuery] = useState(''); const [kind, setKind] = useState(''); const [error, setError] = useState(''); const [busyId, setBusyId] = useState(null)
  const load = async (value, type) => { try { const data = await api(`/products?query=${encodeURIComponent(value ?? query)}&product_type=${encodeURIComponent(type ?? kind)}`, {}, token); setItems(data.items); setError('') } catch (err) { setError(err.message) } }
  useEffect(() => { load('', '') }, [token, refreshKey])
  const search = event => { event.preventDefault(); load() }
  const changeKind = event => { setKind(event.target.value); load(undefined, event.target.value) }
  const toggle = async item => { setBusyId(item.id); try { await api(`/products/${item.id}/state`, { method: 'POST', body: JSON.stringify({ active: !item.active }) }, token); await load() } catch (err) { setError(err.message) } finally { setBusyId(null) } }
  return <section><div className="page-title"><div><h2>Каталог</h2><p>Нажмите на цену, чтобы изменить её. Ручная цена (🔒) сохраняется при повторной выгрузке.</p></div></div><Reprice token={token} onDone={() => load()} /><form className="catalog-search" onSubmit={search}><select value={kind} onChange={changeKind}>{PRODUCT_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Найти товар" maxLength="120" /><button className="secondary">Найти</button></form>{error && <p className="error">{error}</p>}<div className="data-table catalog-table"><div className="thead"><span>Товар</span><span>Регион</span><span>Цена</span><span>Статус</span><span /></div>{items.length ? items.map(item => <div className="trow" key={item.id}><span><b>{item.name}</b><small>{item.product_type}</small></span><span>{item.region || '—'}</span><PriceCell item={item} token={token} onSaved={() => load()} onError={setError} /><span><Badge type={item.active ? 'open' : 'closed'}>{item.active ? 'В продаже' : 'Скрыт'}</Badge></span><button className="secondary" disabled={busyId === item.id} onClick={() => toggle(item)}>{busyId === item.id ? 'Сохраняем…' : item.active ? 'Скрыть' : 'Вернуть'}</button></div>) : <Empty text="Товары не найдены." />}</div></section>
}

function Promocodes({ token, refreshKey }) {
  const [items, setItems] = useState([]); const [code, setCode] = useState(''); const [percent, setPercent] = useState('10'); const [limit, setLimit] = useState(''); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const load = async () => { try { const data = await api('/promocodes', {}, token); setItems(data.items); setError('') } catch (err) { setError(err.message) } }
  useEffect(() => { load() }, [token, refreshKey])
  const create = async event => { event.preventDefault(); setBusy(true); try { await api('/promocodes', { method: 'POST', body: JSON.stringify({ code, discount_percent: Number(percent), usage_limit: limit ? Number(limit) : null }) }, token); setCode(''); setPercent('10'); setLimit(''); await load() } catch (err) { setError(err.message) } finally { setBusy(false) } }
  const toggle = async item => { try { await api(`/promocodes/${item.id}/state`, { method: 'POST', body: JSON.stringify({ active: !item.active }) }, token); await load() } catch (err) { setError(err.message) } }
  return <section><div className="page-title"><div><h2>Промокоды</h2><p>Скидка применяется к товарам до списания бонусов.</p></div></div><form className="promo-form" onSubmit={create}><label>Код<input value={code} onChange={e => setCode(e.target.value.toUpperCase())} placeholder="DKMARKET10" maxLength="40" required /></label><label>Скидка, %<input value={percent} onChange={e => setPercent(e.target.value)} type="number" min="1" max="100" required /></label><label>Лимит использований<input value={limit} onChange={e => setLimit(e.target.value)} type="number" min="1" placeholder="Без лимита" /></label><button className="primary" disabled={busy}>{busy ? 'Создаём…' : 'Создать промокод'}</button></form>{error && <p className="error">{error}</p>}<div className="data-table promo-table"><div className="thead"><span>Код</span><span>Скидка</span><span>Использования</span><span>Статус</span><span /></div>{items.length ? items.map(item => <div className="trow" key={item.id}><span><b>{item.code}</b></span><span>{item.discount_percent}%</span><span>{item.used_count} / {item.usage_limit ?? '∞'}</span><span><Badge type={item.active ? 'open' : 'closed'}>{item.active ? 'Активен' : 'Выключен'}</Badge></span><button className="secondary" onClick={() => toggle(item)}>{item.active ? 'Выключить' : 'Включить'}</button></div>) : <Empty text="Промокодов пока нет." />}</div></section>
}

function Empty({ text }) { return <div className="empty">{text}</div> }

function Support({ token, refreshKey }) {
  const [tickets, setTickets] = useState([]); const [current, setCurrent] = useState(null); const [body, setBody] = useState(''); const [error, setError] = useState(''); const [sending, setSending] = useState(false); const [loading, setLoading] = useState(true); const [loadingTicket, setLoadingTicket] = useState(false)
  const load = async () => { setLoading(true); try { const data = await api('/tickets?include_closed=true', {}, token); setTickets(data.items); if (current) { const fresh = await api(`/tickets/${current.id}`, {}, token); setCurrent(fresh) } } catch (err) { setError(err.message) } finally { setLoading(false) } }
  useEffect(() => { load() }, [token, refreshKey])
  const select = async id => { setLoadingTicket(true); try { setCurrent(await api(`/tickets/${id}`, {}, token)); setBody(''); setError('') } catch (err) { setError(err.message) } finally { setLoadingTicket(false) } }
  const reply = async event => { event.preventDefault(); if (!body.trim() || !current) return; setSending(true); try { await api(`/tickets/${current.id}/reply`, { method: 'POST', body: JSON.stringify({ body }) }, token); setBody(''); await select(current.id); await load() } catch (err) { setError(err.message) } finally { setSending(false) } }
  const close = async () => { if (!current || !window.confirm('Закрыть обращение?')) return; try { await api(`/tickets/${current.id}/close`, { method: 'POST' }, token); await select(current.id); await load() } catch (err) { setError(err.message) } }
  return <section className="support-page"><div className="page-title"><div><h2>Поддержка</h2><p>Ответы сразу сохраняются и уходят пользователю в Telegram.</p></div></div>{error && <p className="error">{error}</p>}<div className={`support-layout ${current ? 'ticket-open' : ''}`}><aside className="ticket-list">{loading ? <Loading text="Загружаем обращения…" /> : tickets.length ? tickets.map(item => <button key={item.id} className={`ticket-item ${current?.id === item.id ? 'selected' : ''}`} onClick={() => select(item.id)}><span><b>{item.public_number}</b><Badge type={item.status}>{item.status === 'open' ? 'Открыто' : 'Закрыто'}</Badge></span><strong>{customerName(item.customer)}</strong><small>{item.message_count} сообщений · {date(item.updated_at)}</small></button>) : <Empty text="Обращений пока нет." />}</aside><article className="conversation">{loadingTicket ? <Loading text="Открываем обращение…" /> : current ? <><header><div><button className="back-to-tickets" onClick={() => setCurrent(null)}>← Все обращения</button><h3>{current.public_number}</h3><p>{customerName(current.customer)}{current.customer?.username && ` · @${current.customer.username}`}</p></div>{current.status === 'open' && <button className="secondary" onClick={close}>Закрыть</button>}</header><div className="messages">{current.messages.map(message => <div className={`message ${message.sender_kind === 'customer' ? 'incoming' : 'outgoing'}`} key={message.id}><p>{message.body}</p><small>{message.sender_kind === 'customer' ? 'Пользователь' : 'Поддержка'} · {date(message.created_at)}</small></div>)}</div>{current.status === 'open' ? <form className="reply-form" onSubmit={reply}><textarea value={body} onChange={e => setBody(e.target.value)} maxLength="2000" placeholder="Напишите ответ…" /><button className="primary" disabled={sending}>{sending ? 'Отправка…' : 'Отправить'}</button></form> : <p className="closed-note">Обращение закрыто. Ответить в него нельзя.</p>}</> : <Empty text="Выберите обращение слева." />}</article></div></section>
}

function App() {
  const [configured, setConfigured] = useState(null); const [token, setToken] = useState(localStorage.getItem('dkmarket-admin-token')); const [tab, setTab] = useState('overview'); const [dashboard, setDashboard] = useState(null); const [users, setUsers] = useState(null); const [orders, setOrders] = useState(null); const [error, setError] = useState(''); const [refresh, setRefresh] = useState(0); const [loading, setLoading] = useState(false)
  useEffect(() => { api('/auth/status').then(data => setConfigured(data.configured)).catch(() => setConfigured(false)) }, [])
  useEffect(() => { const expire = () => { localStorage.removeItem('dkmarket-admin-token'); setToken(null); setError('') }; window.addEventListener('dkmarket-session-expired', expire); return () => window.removeEventListener('dkmarket-session-expired', expire) }, [])
  const load = async () => { if (!token) return; setLoading(true); try { const [overview, userList, orderList] = await Promise.all([api('/dashboard', {}, token), api('/users', {}, token), api('/orders', {}, token)]); setDashboard(overview); setUsers(userList); setOrders(orderList); setError('') } catch (err) { setError(err.message) } finally { setLoading(false) } }
  useEffect(() => { load() }, [token, refresh])
  if (configured === null) return <Loading full text="Проверяем настройки панели…" />
  if (configured === false) return <Setup />
  if (!token) return <Login onLogin={setToken} />
  const tabs = [['overview', 'Обзор', 'Главная'], ['users', 'Пользователи', 'Клиенты'], ['catalog', 'Каталог', 'Товары'], ['orders', 'Заказы', 'Заказы'], ['promos', 'Промокоды', 'Промо'], ['support', 'Поддержка', 'Чаты']]
  const logout = () => { localStorage.removeItem('dkmarket-admin-token'); setToken(null) }
  return <div className="shell"><header className="mobile-brand"><b>DKMARKET</b><button onClick={logout}>Выйти</button></header><aside className="sidebar"><div className="brand"><b>DKMARKET</b><span>Digital<br />Админ-панель</span></div><nav>{tabs.map(([key, label]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}</button>)}</nav><button className="logout" onClick={logout}>Выйти</button></aside><main className="content">{error && <div className="error error-with-action"><span>{error}</span><button onClick={() => setRefresh(value => value + 1)}>Повторить</button></div>}{loading ? <Loading /> : <>{tab === 'overview' && <Overview data={dashboard} />}{tab === 'users' && <Users data={users} />}{tab === 'catalog' && <Catalog token={token} refreshKey={refresh} />}{tab === 'orders' && <Orders data={orders} />}{tab === 'promos' && <Promocodes token={token} refreshKey={refresh} />}{tab === 'support' && <Support token={token} refreshKey={refresh} />}</>}</main><nav className="bottom-nav">{tabs.map(([key, , mobileLabel]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{mobileLabel}</button>)}</nav></div>
}

createRoot(document.getElementById('root')).render(<App />)
