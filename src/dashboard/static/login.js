'use strict';
document.getElementById('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button');
  const message = document.getElementById('login-error');
  button.disabled = true; message.textContent = '';
  try {
    const response = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({password:document.getElementById('password').value})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Gagal masuk.');
    location.href = '/';
  } catch (error) { message.textContent = error.message || 'Koneksi terputus.'; }
  finally { button.disabled = false; document.getElementById('password').value = ''; }
});
