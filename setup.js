(() => {
  const dialog = document.getElementById('setup-dialog');
  const form = document.getElementById('setup-form');
  const steps = [...form.querySelectorAll('.setup-step')];
  const feedback = document.getElementById('setup-feedback');
  const next = document.getElementById('setup-next');
  let step = 0;
  let busy = false;
  let verified = false;
  let permissionsReady = false;
  let modelReady = false;
  let savedSettings = {};
  let browsers = [];
  const field = name => form.elements.namedItem(name);

  function resetConnection() {
    verified = false;
    permissionsReady = false;
    modelReady = false;
    document.getElementById('setup-model-state').textContent = 'Not checked';
    const account = document.getElementById('setup-account-state');
    account.textContent = 'Not verified yet';
    account.classList.remove('ready');
    for (const key of ['accessibility', 'input', 'screen']) {
      const el = document.getElementById('permission-' + key);
      el.textContent = 'Not checked';
      el.classList.remove('ready');
    }
  }

  async function api(path, payload) {
    const response = await fetch(path, payload === undefined ? { cache: 'no-store' } : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Setup could not complete. Please try again.');
    return result;
  }

  function values() {
    return {
      browser: field('browser').value,
      account_handle: field('account_handle').value.trim().replace(/^@/, ''),
      profile_name: field('profile_name').value.trim(),
      profile_description: field('profile_description').value.trim(),
      approved_webpage: field('approved_webpage').value.trim(),
      topics: field('topics').value.split(/[,\n]/).map(s => s.trim()).filter(Boolean),
      attitude: field('attitude').value.trim(),
      posts_per_day: Number(field('posts_per_day').value),
      replies_per_day: field('browser').value === 'arc' ? Number(field('replies_per_day').value) : 0,
      min_gap_hours: savedSettings.min_gap_hours ?? 0.5,
      max_gap_hours: savedSettings.max_gap_hours ?? 2.5,
      check_public_responses: savedSettings.check_public_responses ?? false
    };
  }

  function browserHelp() {
    const arc = field('browser').value === 'arc';
    const installed = browsers.find(b => b.id === field('browser').value)?.installed;
    document.getElementById('setup-browser-help').textContent =
      (installed ? 'Installed. ' : 'Not found in /Applications. Install it before continuing. ') +
      (arc ? 'Recommended for live search/replies. Uses your existing Arc session with visible, interruptible takeovers.' :
        'Uses a separate agent browser profile. Your personal browser session is never copied. Standalone posts only.');
    field('replies_per_day').disabled = !arc;
    document.getElementById('setup-arc-permissions').hidden = !arc;
    document.getElementById('setup-login-help').textContent = arc ?
      `Sign in as @${values().account_handle} in Arc. If X has multiple accounts, use the bottom-left account menu → Add an existing account or switch to this account. Leave that X tab active, then come back and check.` :
      `Sign in as @${values().account_handle} in the dedicated browser window. Resolve any X verification manually. Return here and check the account; do not close the login window first.`;
  }

  function showStep() {
    steps.forEach((section, index) => {
      section.hidden = index !== step;
      [...section.querySelectorAll('input, textarea, select')].forEach(input => {
        input.disabled = index !== step || (input.name === 'replies_per_day' && field('browser').value !== 'arc');
      });
    });
    document.querySelectorAll('#setup-steps li').forEach((item, index) => {
      if (index === step) item.setAttribute('aria-current', 'step');
      else item.removeAttribute('aria-current');
    });
    document.getElementById('setup-progress').textContent = `Step ${step + 1} of ${steps.length} · agent paused during setup`;
    document.getElementById('setup-back').disabled = step === 0 || busy;
    next.textContent = step === 4 ? 'Finish & start agent' : step === 2 ? 'Save & connect account' : 'Continue';
    next.disabled = busy || (step === 3 && (!verified || !modelReady || (field('browser').value === 'arc' && !permissionsReady)));
    if (step === 4) {
      const v = values();
      document.getElementById('setup-review').textContent =
        `Account: @${v.account_handle}\nBrowser: ${v.browser}\nContext: ${v.profile_name}\n${v.profile_description}\n\nTopics: ${v.topics.join(', ')}\nDaily caps: ${v.posts_per_day} posts / ${v.replies_per_day} replies\nPage: ${v.approved_webpage || 'none'}\n\nAttitude:\n${v.attitude}`;
    }
    dialog.scrollTop = 0;
  }

  async function perform(task) {
    if (busy) return;
    busy = true;
    feedback.textContent = 'Working…';
    form.setAttribute('aria-busy', 'true');
    [...dialog.querySelectorAll('button')].forEach(button => { button.disabled = true; });
    try {
      await task();
      feedback.textContent = '';
    } catch (error) {
      feedback.textContent = error.message || 'Setup failed. Please try again.';
    } finally {
      busy = false;
      form.removeAttribute('aria-busy');
      [...dialog.querySelectorAll('button')].forEach(button => { button.disabled = false; });
      showStep();
    }
  }

  async function openSetup(snapshot) {
    await api('/setup/begin', {});
    savedSettings = snapshot.settings || {};
    browsers = snapshot.browsers || [];
    field('browser').value = savedSettings.browser || 'arc';
    field('account_handle').value = savedSettings.account_handle || '';
    field('profile_name').value = savedSettings.profile_name || '';
    field('profile_description').value = savedSettings.profile_description || '';
    field('approved_webpage').value = savedSettings.approved_webpage || '';
    field('topics').value = (savedSettings.topics || []).join('\n');
    field('attitude').value = savedSettings.attitude || snapshot.default_voice;
    field('posts_per_day').value = savedSettings.posts_per_day ?? 3;
    field('replies_per_day').value = savedSettings.replies_per_day ?? 20;
    resetConnection();
    step = 0;
    browserHelp();
    showStep();
    feedback.textContent = '';
    if (!dialog.open) dialog.showModal();
  }

  field('browser').addEventListener('change', browserHelp);
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    perform(async () => {
      if (step === 0 && !/^[A-Za-z0-9_]{1,15}$/.test(values().account_handle)) {
        throw new Error('Enter a valid X handle (1–15 letters, numbers, or underscores).');
      }
      if (step === 2) {
        await api('/setup/save', values());
        resetConnection();
        browserHelp();
      }
      if (step === 4) {
        await api('/setup/complete', {});
        dialog.close();
        document.getElementById('dashboard-feedback').textContent = 'Setup complete. Your agent is starting autonomous publishing within your configured limits.';
        window.location.reload();
        return;
      }
      step = Math.min(4, step + 1);
    });
  });
  document.getElementById('setup-back').onclick = () => {
    step = Math.max(0, step - 1);
    feedback.textContent = '';
    showStep();
  };
  document.getElementById('setup-dismiss').onclick = () => {
    dialog.close();
    document.getElementById('dashboard-feedback').textContent = 'Setup is unfinished. The agent remains paused. Choose Run setup to continue.';
  };
  dialog.addEventListener('cancel', () => {
    document.getElementById('dashboard-feedback').textContent = 'Setup is unfinished. The agent remains paused.';
  });
  document.getElementById('setup-login').onclick = () => perform(async () => {
    await api('/setup/login', {});
    document.getElementById('setup-account-state').textContent = 'Login opened. Sign in manually, then check the account here.';
  });
  document.getElementById('setup-download-model').onclick = () => perform(async () => {
    await api('/setup/model-download', {});
    document.getElementById('setup-model-state').textContent = 'Download started. Keep Ollama open, then click Check model. This may take several minutes.';
  });
  document.getElementById('setup-check-model').onclick = () => perform(async () => {
    modelReady = false;
    const result = await api('/setup/model', {});
    modelReady = Boolean(result.ready);
    document.getElementById('setup-model-state').textContent = modelReady ? 'Local model ready' :
      result.download?.state === 'error' ? result.download.error :
      result.download?.state === 'downloading' ? 'Downloading… check again in a moment.' : 'Model not installed yet. Click Download.';
  });
  document.getElementById('setup-verify').onclick = () => perform(async () => {
    verified = false;
    document.getElementById('setup-account-state').classList.remove('ready');
    document.getElementById('setup-account-state').textContent = 'Checking the signed-in account…';
    const result = await api('/setup/verify', {});
    verified = true;
    const el = document.getElementById('setup-account-state');
    el.textContent = `Verified signed-in account: @${result.account}`;
    el.classList.add('ready');
  });
  document.querySelectorAll('[data-permission]').forEach(button => {
    button.onclick = () => perform(async () => {
      await api('/setup/permission', { destination: button.dataset.permission });
    });
  });
  document.getElementById('setup-check-permissions').onclick = () => perform(async () => {
    permissionsReady = false;
    const result = await api('/setup/permissions', {});
    permissionsReady = ['accessibility', 'input', 'screen'].every(key => result.permissions[key]);
    for (const key of ['accessibility', 'input', 'screen']) {
      const el = document.getElementById('permission-' + key);
      el.textContent = result.permissions[key] ? 'Allowed' : 'Not allowed yet — enable it in System Settings';
      el.classList.toggle('ready', Boolean(result.permissions[key]));
    }
  });
  document.getElementById('run-setup').onclick = async () => {
    try {
      if (!confirm('Pause the agent and open guided setup? Pending publications and drafts will be preserved.')) return;
      await openSetup(await api('/setup'));
    } catch (error) {
      document.getElementById('dashboard-feedback').textContent = error.message;
    }
  };
  api('/setup').then(snapshot => {
    savedSettings = snapshot.settings || {};
    window.agentSettings = savedSettings;
    if (snapshot.required || snapshot.editing) return openSetup(snapshot);
  }).catch(error => {
    document.getElementById('dashboard-feedback').textContent = error.message;
  });
})();
