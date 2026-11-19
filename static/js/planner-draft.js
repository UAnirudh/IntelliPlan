(() => {
  'use strict';

  // Keep the existing photo draft compatible with tabs opened before a release.
  const key = 'ip_photo_planner_draft';
  const lifetime = 120000;
  const maxLength = 12000;

  window.IPPlannerDraft = {
    open(text) {
      const owner = document.body.dataset.userId;
      if (!owner || typeof text !== 'string' || !text.trim() || text.length > maxLength) return false;
      try {
        sessionStorage.setItem(key, JSON.stringify({ owner, text, at: Date.now() }));
      } catch (_) {
        return false;
      }
      window.location.assign('/scheduler?capture=1');
      return true;
    },
    take() {
      try {
        const stored = sessionStorage.getItem(key);
        sessionStorage.removeItem(key);
        const draft = JSON.parse(stored || 'null');
        const owner = document.body.dataset.userId;
        const age = Date.now() - draft?.at;
        if (draft && owner && draft.owner === owner && age >= 0 && age < lifetime &&
            typeof draft.text === 'string' && draft.text.length <= maxLength) return draft.text;
      } catch (_) { /* The manual paste field stays available. */ }
      return '';
    },
  };
})();
