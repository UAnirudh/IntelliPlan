(() => {
  const status = document.getElementById('request-status');
  const button = document.getElementById('request-access');
  const labels = {none: 'You have not submitted a request.', pending: 'Your request is awaiting owner review.', approved: 'Your request was approved.', denied: 'Your request was declined. You may submit another request.', already_admin: 'You already have administrator access.'};
  async function update(method) {
    button.disabled = true;
    try {
      const response = await fetch('/api/lakeside-admin/request', {method, credentials: 'same-origin', headers: {'Content-Type': 'application/json'}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || 'Could not load request status.');
      status.textContent = labels[data.status] || data.status;
      button.hidden = ['pending', 'approved', 'already_admin'].includes(data.status);
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  }
  button.addEventListener('click', () => update('POST'));
  update('GET');
})();
