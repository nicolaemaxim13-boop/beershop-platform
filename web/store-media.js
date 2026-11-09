'use strict';

function productPhotoUrl(value) {
  if (typeof value !== 'string' || !value.trim()) return '';
  try {
    const url = new URL(value, location.origin);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
  } catch { return ''; }
}

function productImage(product) {
  const src = productPhotoUrl(product.image_url);
  const name = safe(product.name);
  return `<div class="art product-media">
    ${src ? `<button class="product-photo-button" type="button" data-photo-src="${safe(src)}" data-photo-name="${name}" aria-label="Переглянути фото: ${name}" title="Переглянути фото">
      <img class="catalog-photo" src="${safe(src)}" alt="${name}" width="600" height="450" loading="lazy" decoding="async" referrerpolicy="no-referrer">
    </button>` : ''}
    <span class="product-photo-fallback" ${src ? 'hidden' : ''}>Фото відсутнє</span>
  </div>`;
}

const photoDialog = document.createElement('dialog');
photoDialog.id = 'product-photo-dialog';
photoDialog.className = 'photo-dialog';
photoDialog.setAttribute('aria-labelledby', 'product-photo-title');
photoDialog.innerHTML = `<div class="photo-dialog-head"><h2 id="product-photo-title"></h2>
  <button type="button" class="photo-close" aria-label="Закрити фото" title="Закрити фото"><img src="/assets/crm-icons/x.svg" alt="" width="20" height="20"></button></div>
  <img class="enlarged-photo" alt="" referrerpolicy="no-referrer">
  <p class="photo-error" role="status" hidden>Фото недоступне</p>`;
document.body.append(photoDialog);
const enlargedPhoto = photoDialog.querySelector('.enlarged-photo');
const photoError = photoDialog.querySelector('.photo-error');
let photoTrigger;

photoDialog.querySelector('.photo-close').addEventListener('click', () => photoDialog.close());
photoDialog.addEventListener('click', event => {
  if (event.target !== photoDialog) return;
  const rect = photoDialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) photoDialog.close();
});
photoDialog.addEventListener('close', () => {
  enlargedPhoto.removeAttribute('src');
  if (photoTrigger?.isConnected) photoTrigger.focus();
});
enlargedPhoto.addEventListener('error', () => {
  enlargedPhoto.hidden = true;
  photoError.hidden = false;
});

document.addEventListener('click', event => {
  const button = event.target.closest('.product-photo-button');
  if (!button) return;
  const src = productPhotoUrl(button.dataset.photoSrc);
  if (!src) return;
  photoTrigger = button;
  document.getElementById('product-photo-title').textContent = button.dataset.photoName;
  enlargedPhoto.alt = button.dataset.photoName;
  enlargedPhoto.hidden = false;
  photoError.hidden = true;
  enlargedPhoto.src = src;
  photoDialog.showModal();
});

// Failed external media must not leave broken images or inactive zoom controls.
document.addEventListener('error', event => {
  if (!event.target.classList?.contains('catalog-photo')) return;
  const media = event.target.closest('.product-media');
  media.querySelector('.product-photo-button').hidden = true;
  const fallback = media.querySelector('.product-photo-fallback');
  fallback.textContent = 'Фото недоступне';
  fallback.hidden = false;
}, true);
