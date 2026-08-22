import { useEffect, useState } from 'react'

/** Shows the real logo image if it exists on disk; falls back to a plain
 * text badge (current behavior) if the file hasn't been provided yet.
 * Preloads via a JS Image() first rather than relying on <img onError>,
 * since Vite's dev server can return an ambiguous (non-404) response for
 * missing files under /src/, which doesn't reliably fire onError. */
export default function BrandLogo({ src, fallbackText, className }) {
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    const img = new Image()
    img.onload = () => {
      // a real image has non-zero natural dimensions; Vite's SPA fallback
      // HTML (served as a fake "success") would not decode as an image at all
      if (!cancelled && img.naturalWidth > 0) setLoaded(true)
    }
    img.onerror = () => {}
    img.src = src
    return () => {
      cancelled = true
    }
  }, [src])

  if (!loaded) {
    return <div className={className}>{fallbackText}</div>
  }

  return <img src={src} alt={fallbackText} className={className} />
}
