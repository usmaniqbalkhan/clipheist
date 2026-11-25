import { PHOTOS, drawnScene } from '../lib/scenes.js';

// An illustration slot: shows the matching photo from /images, or the drawn scene if there is none
// (or the photo fails to load). Hero images load eagerly; everything else lazily.
export default function SceneImg({ scene, orient, className = '', alt = '', ...rest }) {
  const vertical = orient === 'v';
  const set = vertical ? 'v' : className.split(' ').includes('bg') ? 'bg' : 'h';
  const photo = PHOTOS[set][scene];
  const eager = className.split(' ').includes('content_img');
  return (
    <img
      {...rest}
      className={className || undefined}
      alt={alt}
      data-scene={scene}
      data-orient={orient}
      decoding="async"
      loading={eager ? 'eager' : 'lazy'}
      src={photo ? `/images/${photo}.webp` : drawnScene(scene, vertical)}
      onError={photo ? (e) => { e.currentTarget.onerror = null; e.currentTarget.src = drawnScene(scene, vertical); } : undefined}
    />
  );
}
