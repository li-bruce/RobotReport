/* Reveal the sign-in screen only after its responsive artwork has decoded. */
(() => {
  'use strict';
  const template = document.querySelector('.gather-login-loader').cloneNode(true);
  const images = new Map();
  const mobileQuery = '(max-width:760px), (max-width:1000px) and (orientation:portrait)';
  let active = null;

  function preload(url) {
    if (!images.has(url)) {
      const image = new Image();
      image.decoding = 'async';
      image.fetchPriority = 'high';
      const loaded = new Promise((resolve, reject) => {
        image.onload = async () => {
          try {
            if (image.decode) await image.decode();
            if (!image.naturalWidth) throw new Error('Empty image');
            resolve();
          } catch (error) { reject(error); }
        };
        image.onerror = () => reject(new Error('Image unavailable'));
        image.src = url;
      }).catch(error => { images.delete(url); throw error; });
      images.set(url, loaded);
    }
    return images.get(url);
  }

  function dispose() {
    if (active) active();
    active = null;
  }

  function mount(root) {
    dispose();
    const loader = template.cloneNode(true);
    const content = [root.querySelector('.gather-login-title'), root.querySelector('.gather-login-access')];
    const status = loader.querySelector('.gather-loading-status');
    const actions = loader.querySelector('.gather-loading-actions');
    const retry = loader.querySelector('[data-loading-retry]');
    const media = window.matchMedia(mobileQuery);
    let generation = 0;
    let timer;
    let previousFocus;
    root.append(loader);

    function reveal(fallback = false) {
      ++generation;
      clearTimeout(timer);
      if (!root.isConnected) return;
      if (fallback) root.style.backgroundImage = 'none';
      root.dataset.loginState = fallback ? 'fallback' : 'ready';
      root.classList.replace('is-loading', 'is-ready');
      root.setAttribute('aria-busy', 'false');
      const focusLogin = loader.contains(document.activeElement);
      loader.inert = true;
      loader.setAttribute('aria-hidden', 'true');
      content.forEach(el => { el.inert = false; });
      if (focusLogin) root.querySelector('input').focus({preventScroll:true});
      else if (previousFocus?.isConnected && root.contains(previousFocus)) previousFocus.focus({preventScroll:true});
      previousFocus = null;
    }

    function offerContinue(message, canRetry) {
      status.textContent = message;
      actions.hidden = false;
      retry.hidden = !canRetry;
    }

    function begin() {
      const current = ++generation;
      clearTimeout(timer);
      if (!root.isConnected) return;
      if (root.contains(document.activeElement) && !loader.contains(document.activeElement)) previousFocus = document.activeElement;
      root.style.removeProperty('background-image');
      const url = getComputedStyle(root).backgroundImage.match(/url\(["']?(.*?)["']?\)/)?.[1];
      root.dataset.loginState = 'loading';
      root.classList.remove('is-ready');
      root.classList.add('is-loading');
      root.setAttribute('aria-busy', 'true');
      content.forEach(el => { el.inert = true; });
      loader.inert = false;
      loader.removeAttribute('aria-hidden');
      actions.hidden = true;
      status.textContent = '正在加载';
      timer = setTimeout(() => {
        if (generation !== current || !root.isConnected) return;
        root.dataset.loginState = 'slow';
        offerContinue('加载时间较长', false);
      }, 15000);
      (url ? preload(url) : Promise.reject(new Error('Missing image'))).then(() => {
        if (generation === current) reveal();
      }).catch(() => {
        if (generation !== current || !root.isConnected) return;
        clearTimeout(timer);
        root.dataset.loginState = 'error';
        offerContinue('背景加载失败', true);
      });
    }

    retry.addEventListener('click', begin);
    loader.querySelector('[data-loading-continue]').addEventListener('click', () => reveal(true));
    media.addEventListener('change', begin);
    active = () => { ++generation; clearTimeout(timer); media.removeEventListener('change', begin); };
    begin();
  }

  window.GatherLogin = {mount, dispose};
})();
