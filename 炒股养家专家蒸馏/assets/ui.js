/* 炒股养家专家蒸馏 · 交互逻辑（复制提示词 + Mermaid 初始化） */
(function () {
  // --- Mermaid 初始化 ---
  if (window.mermaid) {
    mermaid.initialize({
      startOnLoad: true,
      theme: 'neutral',
      securityLevel: 'loose',
      flowchart: { useMaxWidth: true, htmlLabels: true, curve: 'basis' }
    });
  }

  // --- 载入提示词全文 ---
  var src = document.getElementById('prompt-source');
  var block = document.getElementById('prompt-block');
  if (src && block) {
    block.textContent = src.textContent.replace(/^\n+|\n+$/g, '');
  }

  // --- 复制提示词（含 file:// 环境的降级方案） ---
  var btn = document.getElementById('copy-prompt');
  var hint = document.getElementById('copy-hint');
  if (btn && src) {
    btn.addEventListener('click', function () {
      var text = src.textContent.replace(/^\n+|\n+$/g, '');
      var done = false;

      function feedback(ok) {
        if (ok) {
          hint.textContent = '已复制到剪贴板，去粘贴吧';
          setTimeout(function () {
            hint.textContent = '粘贴到任意大模型的 System Prompt / 自定义指令即可使用';
          }, 2200);
        } else {
          hint.textContent = '复制失败，请手动全选下方文本框内容';
        }
      }

      function fallbackCopy(t) {
        var ta = document.createElement('textarea');
        ta.value = t;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.top = '0';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        var ok = false;
        try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
        document.body.removeChild(ta);
        return ok;
      }

      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(function () {
          feedback(true);
        }).catch(function () {
          feedback(fallbackCopy(text));
        });
      } else {
        feedback(fallbackCopy(text));
      }
    });
  }
})();
