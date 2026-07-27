/**
 * The detected skeleton, drawn over the photo on a canvas.
 *
 * **Client-side, deliberately.** The alternative — the server drawing on the image and returning
 * a second file — costs an extra round trip, server-side encoding, storage for a derived
 * artifact, and a cache-invalidation question every time the rules change. The landmarks are
 * already in the response; drawing them is a rendering concern. The legacy `draw()` did the
 * opposite, calling `cv2.imread` a second time on an image it had already loaded and writing an
 * annotated copy to disk.
 *
 * Two things are easy to get wrong here and both are handled explicitly:
 *
 * **Scaling.** Landmarks are fractions of the image, and the image is laid out by CSS at
 * whatever size the viewport allows. The canvas is sized from the *rendered* box, measured after
 * layout, so the overlay tracks the photo through any aspect ratio and any resize.
 *
 * **Device pixel ratio.** A canvas has two sizes: its backing store (`width`/`height`) and its
 * CSS box. On a high-DPI display they differ by `devicePixelRatio`, and ignoring that draws a
 * blurry skeleton at half scale in the top-left corner.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { AnalysisResponse } from '../api/types'
import styles from './SkeletonOverlay.module.css'

type Landmark = AnalysisResponse['landmarks'][number]

/**
 * The bones, as pairs of landmark names.
 *
 * A segment is drawn only when *both* of its ends are `ok`. A line between a measured shoulder
 * and a guessed elbow looks exactly like a measured one, and there is no way to caveat a line.
 */
const SEGMENTS: ReadonlyArray<readonly [string, string]> = [
  ['left_shoulder', 'right_shoulder'],
  ['left_shoulder', 'left_elbow'],
  ['left_elbow', 'left_wrist'],
  ['right_shoulder', 'right_elbow'],
  ['right_elbow', 'right_wrist'],
  ['left_shoulder', 'left_hip'],
  ['right_shoulder', 'right_hip'],
  ['left_hip', 'right_hip'],
  ['left_hip', 'left_knee'],
  ['left_knee', 'left_ankle'],
  ['right_hip', 'right_knee'],
  ['right_knee', 'right_ankle'],
  ['left_ankle', 'left_heel'],
  ['left_heel', 'left_foot_index'],
  ['right_ankle', 'right_heel'],
  ['right_heel', 'right_foot_index'],
  ['nose', 'neck'],
  ['neck', 'left_shoulder'],
  ['neck', 'right_shoulder'],
]

const CONFIDENT_COLOUR = '#2ec4b6'
const UNCERTAIN_COLOUR = '#f4a261'
const CONFIDENT_RADIUS = 4
const UNCERTAIN_RADIUS = 3

interface Props {
  imageUrl: string
  landmarks: Landmark[]
  /** Alt text for the photo underneath. */
  alt?: string
}

export default function SkeletonOverlay({
  imageUrl,
  landmarks,
  alt = 'The photo you uploaded',
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const imageRef = useRef<HTMLImageElement | null>(null)
  const [box, setBox] = useState<{ width: number; height: number } | null>(null)

  // Measured after layout rather than taken from the natural image size, because CSS decides how
  // big the photo actually is. `ResizeObserver` rather than a window `resize` listener: the image
  // can change size without the window doing so — a sidebar opening, a font loading, a flex
  // container reflowing — and a window listener misses all of those.
  useEffect(() => {
    const image = imageRef.current
    if (!image) return

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      const { width, height } = entry.contentRect
      if (width > 0 && height > 0) setBox({ width, height })
    })
    observer.observe(image)
    return () => observer.disconnect()
  }, [imageUrl])

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas || !box) return

    const context = canvas.getContext('2d')
    if (!context) return

    // The backing store is in device pixels; everything drawn afterwards is in CSS pixels because
    // of the transform below. Without this the skeleton renders at 1/dpr scale on a Retina
    // display — correct maths, wrong coordinate space.
    const ratio = window.devicePixelRatio || 1
    canvas.width = Math.round(box.width * ratio)
    canvas.height = Math.round(box.height * ratio)
    canvas.style.width = `${box.width}px`
    canvas.style.height = `${box.height}px`

    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    context.clearRect(0, 0, box.width, box.height)

    const byName = new Map(landmarks.map((landmark) => [landmark.name, landmark]))
    const at = (landmark: Landmark) => ({
      x: landmark.x * box.width,
      y: landmark.y * box.height,
    })

    context.lineWidth = 2
    context.strokeStyle = CONFIDENT_COLOUR
    for (const [from, to] of SEGMENTS) {
      const start = byName.get(from)
      const end = byName.get(to)
      // Both ends measured, or no line. See the SEGMENTS comment.
      if (!start || !end || start.status !== 'ok' || end.status !== 'ok') continue

      const a = at(start)
      const b = at(end)
      context.beginPath()
      context.moveTo(a.x, a.y)
      context.lineTo(b.x, b.y)
      context.stroke()
    }

    for (const landmark of landmarks) {
      // Not drawn at all. A point the model never found has no position to draw, and inventing
      // one is the whole class of error this project is about.
      if (landmark.status === 'not_detected' || landmark.status === 'out_of_frame') continue

      const confident = landmark.status === 'ok'
      const { x, y } = at(landmark)

      context.beginPath()
      context.arc(x, y, confident ? CONFIDENT_RADIUS : UNCERTAIN_RADIUS, 0, Math.PI * 2)
      context.fillStyle = confident ? CONFIDENT_COLOUR : UNCERTAIN_COLOUR
      context.fill()

      if (!confident) {
        // Hollow, so an uncertain point is distinguishable without relying on colour alone.
        context.strokeStyle = UNCERTAIN_COLOUR
        context.lineWidth = 1.5
        context.stroke()
        context.strokeStyle = CONFIDENT_COLOUR
        context.lineWidth = 2
      }
    }
  }, [box, landmarks])

  useEffect(draw, [draw])

  return (
    <figure className={styles.figure}>
      <img
        ref={imageRef}
        src={imageUrl}
        alt={alt}
        className={styles.image}
        // Redraw once the bitmap is decoded: before that the element has no intrinsic size, so
        // the observed box would be the CSS box of an empty image.
        onLoad={draw}
      />
      {/* `aria-hidden`: the skeleton is decoration over a photo that already has alt text, and a
          canvas has nothing meaningful to announce. The measurements are the accessible content
          and they are rendered as text beside this. */}
      <canvas ref={canvasRef} className={styles.canvas} aria-hidden="true" data-testid="skeleton" />
    </figure>
  )
}
