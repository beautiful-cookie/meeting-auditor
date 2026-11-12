// Тема до отрисовки, чтобы не мигала: light | dark; без записи — как в системе.
// Отдельным файлом, а не встроенным скриптом: политика безопасности (CSP) запрещает inline-скрипты.
try {
  const t = localStorage.getItem("theme");
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
} catch (_) { /* приватный режим — просто тема по системе */ }
