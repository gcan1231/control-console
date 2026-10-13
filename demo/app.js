const mode = new URLSearchParams(location.search).get('workspace') === 'demo' ? 'demo' : 'local';
document.addEventListener('change', event => {
  if (event.target.id !== 'workspace-mode') return;
  const url = new URL(location.href);
  url.searchParams.set('workspace', event.target.value);
  location.assign(url);
});
if (mode === 'demo') {
  const controls = document.querySelector('.heading-controls');
  controls.insertAdjacentHTML('afterbegin','<label for="workspace-mode">工作空间</label><select id="workspace-mode"><option value="local">本地任务</option><option value="demo" selected>界面演示</option></select>');
  await import('./demo-app.js');
} else {
  const {mountLocalWorkspace} = await import('./modules/local-workspace.js');
  await mountLocalWorkspace();
}
