const connections = document.querySelector('.orbit');
const description = document.getElementById('connection-detail');
if (connections && description) {
  connections.addEventListener('click', event => {
    const source = event.target.closest('button[data-description]');
    if (!source) return;
    for (const button of connections.querySelectorAll('button')) {
      button.setAttribute('aria-pressed', String(button === source));
    }
    description.textContent = source.dataset.description;
  });
}
