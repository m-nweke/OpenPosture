import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import SkeletonOverlay from './SkeletonOverlay'
import { landmarksWithGaps } from '../test/reports'
import type { AnalysisResponse } from '../api/types'

type Landmark = AnalysisResponse['landmarks'][number]

/**
 * A recording 2D context.
 *
 * jsdom has no canvas implementation, so this stands in for one — and it is better than a real
 * one for these assertions anyway. What matters is *which* primitives were issued and at what
 * coordinates, and a recorder answers that directly where a rendered bitmap would have to be
 * pixel-diffed.
 */
function recordingContext() {
  const arcs: Array<{ x: number; y: number; radius: number }> = []
  const lines: Array<{ from: [number, number]; to: [number, number] }> = []
  const transforms: number[][] = []
  let pen: [number, number] = [0, 0]
  const fills: string[] = []

  const context = {
    canvas: null as unknown as HTMLCanvasElement,
    lineWidth: 1,
    strokeStyle: '',
    fillStyle: '',
    setTransform(a: number, b: number, c: number, d: number, e: number, f: number) {
      transforms.push([a, b, c, d, e, f])
    },
    clearRect() {},
    beginPath() {},
    moveTo(x: number, y: number) {
      pen = [x, y]
    },
    lineTo(x: number, y: number) {
      lines.push({ from: pen, to: [x, y] })
    },
    stroke() {},
    arc(x: number, y: number, radius: number) {
      arcs.push({ x, y, radius })
    },
    fill() {
      fills.push(String(context.fillStyle))
    },
  }

  return { context, arcs, lines, transforms, fills }
}

const BOX = { width: 400, height: 300 }

let recorder: ReturnType<typeof recordingContext>

beforeEach(() => {
  recorder = recordingContext()
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
    recorder.context as unknown as CanvasRenderingContext2D,
  )
  // The overlay measures the rendered image, which jsdom lays out at 0x0. Reporting a real box
  // is what makes the scaling assertions meaningful rather than all-zero.
  vi.spyOn(HTMLImageElement.prototype, 'getBoundingClientRect').mockReturnValue({
    ...new DOMRect(0, 0, BOX.width, BOX.height),
    toJSON: () => ({}),
  })
  stubResizeObserver()
})

afterEach(() => {
  vi.restoreAllMocks()
})

/** Fire the observer immediately with a known box, so the component has a size to draw against. */
function stubResizeObserver() {
  class ImmediateResizeObserver {
    // A plain field, not a parameter property: `erasableSyntaxOnly` rejects the shorthand
    // because it emits runtime code rather than erasing to nothing.
    private readonly callback: ResizeObserverCallback

    constructor(callback: ResizeObserverCallback) {
      this.callback = callback
    }

    observe() {
      this.callback(
        [{ contentRect: { width: BOX.width, height: BOX.height } } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      )
    }
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal('ResizeObserver', ImmediateResizeObserver)
}

function renderOverlay(landmarks: Landmark[] = landmarksWithGaps()) {
  return render(<SkeletonOverlay imageUrl="blob:photo" landmarks={landmarks} />)
}

describe('SkeletonOverlay', () => {
  it('draws one point per landmark that has a position worth drawing', () => {
    // The fixture is six `ok`, one `low_confidence`, one `not_detected`. The last has no measured
    // position, so drawing it would mean inventing one.
    renderOverlay()

    expect(recorder.arcs).toHaveLength(7)
  })

  it('draws nothing for a keypoint the model never found', () => {
    const landmarks = landmarksWithGaps()
    const missing = landmarks.filter((landmark) => landmark.status === 'not_detected')
    expect(missing).toHaveLength(1)

    renderOverlay()

    const drawnAt = recorder.arcs.map((arc) => `${arc.x},${arc.y}`)
    const wouldBe = `${missing[0]!.x * BOX.width},${missing[0]!.y * BOX.height}`
    expect(drawnAt).not.toContain(wouldBe)
  })

  it('renders an uncertain point differently from a measured one', () => {
    // Presenting a guess identically to a measurement is the confident-wrong-answer failure this
    // project exists to remove. Size *and* colour differ, so it does not rest on colour alone.
    renderOverlay()

    const radii = new Set(recorder.arcs.map((arc) => arc.radius))
    expect(radii.size).toBe(2)
    expect(new Set(recorder.fills).size).toBe(2)
  })

  it('scales normalised coordinates onto the rendered box', () => {
    // Landmarks are fractions of the image; the image is laid out by CSS. A point at x=0.4 in a
    // 400px-wide box belongs at 160px, whatever the photo's intrinsic size.
    renderOverlay([
      {
        name: 'left_shoulder',
        x: 0.4,
        y: 0.3,
        status: 'ok',
        visibility: 0.9,
        presence: 0.9,
      },
    ])

    expect(recorder.arcs[0]).toMatchObject({ x: 0.4 * BOX.width, y: 0.3 * BOX.height })
  })

  it('scales correctly at a different aspect ratio', () => {
    // The same fraction maps to different pixels in a different box, which is the property that
    // keeps the overlay on the body when the layout changes.
    const landmark: Landmark = {
      name: 'left_hip',
      x: 0.25,
      y: 0.75,
      status: 'ok',
      visibility: 0.9,
      presence: 0.9,
    }

    renderOverlay([landmark])

    expect(recorder.arcs[0]?.x).toBeCloseTo(0.25 * BOX.width)
    expect(recorder.arcs[0]?.y).toBeCloseTo(0.75 * BOX.height)
  })

  it('accounts for device pixel ratio', () => {
    // A canvas has a backing store and a CSS box. On a high-DPI display they differ, and ignoring
    // that draws a blurry skeleton at half scale in the corner.
    vi.stubGlobal('devicePixelRatio', 2)

    renderOverlay()

    const canvas = screen.getByTestId('skeleton') as HTMLCanvasElement
    expect(canvas.width).toBe(BOX.width * 2)
    expect(canvas.height).toBe(BOX.height * 2)
    expect(canvas.style.width).toBe(`${BOX.width}px`)
    // The transform is what keeps drawing coordinates in CSS pixels despite the larger store.
    expect(recorder.transforms.at(-1)).toEqual([2, 0, 0, 2, 0, 0])
  })

  it('draws a bone only when both of its ends are measured', () => {
    // A line between a measured shoulder and a guessed elbow looks exactly like a measured one,
    // and there is no way to caveat a line.
    renderOverlay()

    const uncertain = landmarksWithGaps().find((landmark) => landmark.status !== 'ok')!
    const endpoint = `${uncertain.x * BOX.width},${uncertain.y * BOX.height}`
    const touched = recorder.lines.some(
      ({ from, to }) => from.join(',') === endpoint || to.join(',') === endpoint,
    )

    expect(touched).toBe(false)
    // Sanity: bones between confident points *are* drawn, so the assertion above is not vacuous.
    expect(recorder.lines.length).toBeGreaterThan(0)
  })

  it('is hidden from assistive technology', () => {
    // Decoration over a photo that already has alt text. The measurements are the accessible
    // content and they are rendered as text beside this.
    renderOverlay()

    expect(screen.getByTestId('skeleton')).toHaveAttribute('aria-hidden', 'true')
    expect(screen.getByRole('img')).toHaveAccessibleName('The photo you uploaded')
  })
})
