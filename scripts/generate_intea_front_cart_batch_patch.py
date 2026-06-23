from __future__ import annotations

import difflib
import os
import re
from pathlib import Path


FRONT_ROOT = Path(r"C:\Users\User\.codex\worktrees\intea-front\main-test")
PATCH_PATH = Path(r"C:\Users\User\Desktop\AI 프로젝트\int_ai-render_web\patches\intea-front-cart-simple-batch.patch")
FRONT_ROOT = Path(
    os.environ.get(
        "INTEA_FRONT_ROOT",
        r"C:\Users\User\.codex\worktrees\intea-front\main-test",
    )
)
PATCH_PATH = Path(__file__).resolve().parents[1] / "patches" / "intea-front-cart-simple-batch.patch"


def read(rel: str) -> str:
    return (FRONT_ROOT / rel).read_text(encoding="utf-8")


def split_lines(value: str) -> list[str]:
    return value.splitlines(keepends=True)


def diff_modified(rel: str, old: str, new: str) -> str:
    lines: list[str] = [f"diff --git a/{rel} b/{rel}\n"]
    lines.extend(
        difflib.unified_diff(
            split_lines(old),
            split_lines(new),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            lineterm="",
        )
    )
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def diff_new(rel: str, new: str) -> str:
    lines: list[str] = [
        f"diff --git a/{rel} b/{rel}\n",
        "new file mode 100644\n",
        "index 0000000..1111111\n",
    ]
    lines.extend(
        difflib.unified_diff(
            [],
            split_lines(new),
            fromfile="/dev/null",
            tofile=f"b/{rel}",
            lineterm="",
        )
    )
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def build_render_ts_patch() -> tuple[str, str, str]:
    rel = "src/lib/ai-consultant/render.ts"
    old = read(rel)
    old_snippet = """export const EXTERNAL_CART_RENDER_PATH = '/api/external/render/cart-simple'

export const getExternalCartRenderUrl = (baseUrl?: string) =>
  `${normalizeRenderBaseUrl(baseUrl)}${EXTERNAL_CART_RENDER_PATH}`
"""
    new_snippet = """export const EXTERNAL_CART_RENDER_PATH = '/api/external/render/cart-simple'
export const EXTERNAL_CART_BATCH_RENDER_PATH = '/api/external/render/cart-simple-batch'

export const getExternalCartRenderUrl = (baseUrl?: string) =>
  `${normalizeRenderBaseUrl(baseUrl)}${EXTERNAL_CART_RENDER_PATH}`

export const getExternalCartBatchRenderUrl = (baseUrl?: string) =>
  `${normalizeRenderBaseUrl(baseUrl)}${EXTERNAL_CART_BATCH_RENDER_PATH}`
"""
    new = old.replace(old_snippet, new_snippet)
    if new == old:
        raise RuntimeError("render.ts replacement failed")
    return rel, old, new


NEW_BATCH_ROUTE = r"""import type { NextApiRequest, NextApiResponse } from 'next'

import {
  buildCartSimpleItemsFromProductIds,
  getExternalCartBatchRenderUrl,
  normalizeCartSimpleItems,
  normalizeRenderBaseUrl,
} from '@/lib/ai-consultant/render'
import type { ConsultantCartSimpleItem } from '@/lib/ai-consultant/render'
import {
  recordAiConsultantDiagnostic,
  recordAiConsultantTraceEvent,
} from '@/lib/ai-consultant/diagnostics'
import { verifyConsultantLogin } from '@/lib/ai-consultant/server'

export const config = {
  api: {
    bodyParser: {
      sizeLimit: '16mb',
    },
  },
}

type CartSimpleBatchVariantBody = {
  productIds?: string[]
  products?: Parameters<typeof normalizeCartSimpleItems>[0]
  room?: string
  style?: string
  variant?: string
  dimensions?: string
  placement?: string
}

type CartSimpleBatchRequestBody = {
  imageUrl?: string
  variants?: CartSimpleBatchVariantBody[]
  room?: string
  style?: string
  variant?: string
  dimensions?: string
  placement?: string
  traceId?: string
}

const RENDER_REQUEST_FAILURE_MESSAGE = 'AI design render request failed.'
const DEFAULT_RENDER_TIMEOUT_MS = 10 * 60 * 1000
const PRODUCT_DETAIL_TIMEOUT_MS = 10 * 1000

const getRenderTimeoutMs = () => {
  const configuredTimeout = Number(process.env.AI_CONSULTANT_RENDER_TIMEOUT_MS)

  return Number.isFinite(configuredTimeout) && configuredTimeout > 0
    ? configuredTimeout
    : DEFAULT_RENDER_TIMEOUT_MS
}

const fetchWithTimeout = async (input: RequestInfo | URL, init?: RequestInit) => {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), getRenderTimeoutMs())

  try {
    return await fetch(input, {
      ...init,
      signal: init?.signal ?? controller.signal,
    })
  } finally {
    clearTimeout(timeoutId)
  }
}

const fetchProductDetailWithTimeout = async (input: RequestInfo | URL) => {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), PRODUCT_DETAIL_TIMEOUT_MS)

  try {
    return await fetch(input, {
      signal: controller.signal,
    })
  } finally {
    clearTimeout(timeoutId)
  }
}

const getCommerceApiBaseUrl = () => {
  if (process.env.AI_CONSULTANT_COMMERCE_API_URL) {
    return normalizeRenderBaseUrl(process.env.AI_CONSULTANT_COMMERCE_API_URL)
  }

  if (process.env.APP_ENV === 'production') {
    return 'https://api.interiorteacher.com'
  }

  return 'https://api.stage-interiorteacher.com'
}

const readFiniteNumber = (value: unknown) => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }

  if (typeof value === 'string') {
    const parsed = Number(value.replace(/[^\d.-]/g, ''))

    if (Number.isFinite(parsed)) {
      return parsed
    }
  }

  return undefined
}

const toMmFromCm = (value: unknown) => {
  const number = readFiniteNumber(value)

  return typeof number === 'number' ? Math.round(number * 10) : undefined
}

const hasCompleteDimensions = (item: ConsultantCartSimpleItem) => {
  const dims = item.dims_mm

  return Boolean(
    dims &&
      typeof dims.width_mm === 'number' &&
      dims.width_mm > 0 &&
      typeof dims.depth_mm === 'number' &&
      dims.depth_mm > 0 &&
      typeof dims.height_mm === 'number' &&
      dims.height_mm >= 0,
  )
}

const getCommerceProductId = (item: ConsultantCartSimpleItem) => {
  const id = item.id.trim()

  return /^\d+$/.test(id) ? id : undefined
}

const readProductDetailDimensions = (detail: unknown) => {
  if (!detail || typeof detail !== 'object') {
    return undefined
  }

  const record = detail as Record<string, unknown>
  const width = toMmFromCm(record.sizeWidth)
  const depth = toMmFromCm(record.sizeDepth)
  const height = toMmFromCm(record.sizeHeight)

  if (!width || !depth || typeof height !== 'number' || height < 0) {
    return undefined
  }

  return {
    width_mm: width,
    depth_mm: depth,
    height_mm: height,
  }
}

const fetchProductDimensions = async (
  productId: string,
): Promise<ConsultantCartSimpleItem['dims_mm'] | undefined> => {
  const response = await fetchProductDetailWithTimeout(
    `${getCommerceApiBaseUrl()}/product/${productId}`,
  )

  if (!response.ok) {
    throw new Error(`product detail request failed with ${response.status}`)
  }

  return readProductDetailDimensions(await response.json())
}

const hydrateItemsWithCommerceDimensions = async (items: ConsultantCartSimpleItem[]) => {
  const cache = new Map<string, ConsultantCartSimpleItem['dims_mm'] | undefined>()

  return Promise.all(
    items.map(async (item) => {
      if (hasCompleteDimensions(item)) {
        return item
      }

      const productId = getCommerceProductId(item)

      if (!productId) {
        return item
      }

      try {
        if (!cache.has(productId)) {
          cache.set(productId, await fetchProductDimensions(productId))
        }

        const dims = cache.get(productId)

        return dims ? { ...item, dims_mm: dims } : item
      } catch (error) {
        recordAiConsultantDiagnostic('render.product_dimensions.error', {
          productId,
          message: error instanceof Error ? error.message : String(error),
        })

        return item
      }
    }),
  )
}

const getRenderConfig = () => {
  const baseUrl = normalizeRenderBaseUrl(process.env.AI_RENDER_API_BASE_URL)
  const apiKey = process.env.AI_RENDER_EXTERNAL_API_KEY

  if (!baseUrl) {
    throw new Error('AI_RENDER_API_BASE_URL is not configured')
  }

  if (!apiKey) {
    throw new Error('AI_RENDER_EXTERNAL_API_KEY is not configured')
  }

  return { baseUrl, apiKey }
}

const pickItems = (variant: CartSimpleBatchVariantBody) => {
  const suppliedItems = normalizeCartSimpleItems(variant.products)

  if (suppliedItems.length > 0) {
    return suppliedItems
  }

  return buildCartSimpleItemsFromProductIds(variant.productIds ?? [])
}

const parseJsonResponse = (responseText: string) => {
  if (!responseText) {
    return {}
  }

  try {
    return JSON.parse(responseText)
  } catch {
    return { message: responseText }
  }
}

const valueWithFallback = (
  variantValue: string | undefined,
  requestValue: string | undefined,
) => (variantValue && variantValue.trim() ? variantValue : requestValue)

const handler = async (req: NextApiRequest, res: NextApiResponse) => {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST')
    res.status(405).json({ message: 'Method not allowed' })
    return
  }

  const isLoggedIn = await verifyConsultantLogin(req)

  if (!isLoggedIn) {
    res.status(401).json({ message: 'Login is required to use AI consultant.' })
    return
  }

  const body = (req.body ?? {}) as CartSimpleBatchRequestBody
  const imageUrl = body.imageUrl?.trim()
  const traceId = typeof body.traceId === 'string' ? body.traceId : undefined
  const variants = Array.isArray(body.variants) ? body.variants.slice(0, 3) : []

  if (!imageUrl) {
    res.status(400).json({ message: 'Customer room image URL is required.' })
    return
  }

  if (variants.length === 0) {
    res.status(400).json({ message: 'Recommendation variants are required for AI design.' })
    return
  }

  const hydratedVariants = await Promise.all(
    variants.map(async (variant, index) => {
      const items = await hydrateItemsWithCommerceDimensions(pickItems(variant))

      return {
        variant_index: index + 1,
        items,
        room: valueWithFallback(variant.room, body.room),
        style: valueWithFallback(variant.style, body.style),
        variant: valueWithFallback(variant.variant, body.variant) ?? String(index + 1),
        dimensions: valueWithFallback(variant.dimensions, body.dimensions),
        placement: valueWithFallback(variant.placement, body.placement),
      }
    }),
  )

  const missingItemsIndex = hydratedVariants.findIndex(
    (variant) => variant.items.length === 0,
  )

  if (missingItemsIndex >= 0) {
    res.status(400).json({
      message: `Recommendation variant ${missingItemsIndex + 1} requires product items.`,
    })
    return
  }

  try {
    const { baseUrl, apiKey } = getRenderConfig()
    recordAiConsultantDiagnostic('render.batch_proxy.request', {
      traceId,
      variantCount: hydratedVariants.length,
      itemCounts: hydratedVariants.map((variant) => variant.items.length),
      hasImageUrl: Boolean(imageUrl),
    })
    await recordAiConsultantTraceEvent(traceId, 'render.cart-simple-batch.request', {
      variantCount: hydratedVariants.length,
      imageUrl,
      variants: hydratedVariants,
      room: body.room,
      style: body.style,
      variant: body.variant,
      dimensions: body.dimensions,
      placement: body.placement,
    })
    const startedAt = Date.now()
    const renderResponse = await fetchWithTimeout(getExternalCartBatchRenderUrl(baseUrl), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey,
      },
      body: JSON.stringify({
        image_url: imageUrl,
        room: body.room,
        style: body.style,
        variant: body.variant,
        dimensions: body.dimensions,
        placement: body.placement,
        variants: hydratedVariants.map((variant) => ({
          items: variant.items,
          room: variant.room,
          style: variant.style,
          variant: variant.variant,
          dimensions: variant.dimensions,
          placement: variant.placement,
        })),
      }),
    })

    const responseText = await renderResponse.text()
    const data = parseJsonResponse(responseText)
    recordAiConsultantDiagnostic('render.batch_proxy.response', {
      traceId,
      status: renderResponse.status,
      ok: renderResponse.ok,
      elapsedMs: Date.now() - startedAt,
      message: typeof data.message === 'string' ? data.message : undefined,
    })
    await recordAiConsultantTraceEvent(traceId, 'render.cart-simple-batch.response', {
      status: renderResponse.status,
      ok: renderResponse.ok,
      elapsedMs: Date.now() - startedAt,
      body: data,
    })

    if (!renderResponse.ok) {
      res.status(renderResponse.status).json({
        ...data,
        message: RENDER_REQUEST_FAILURE_MESSAGE,
      })
      return
    }

    res.status(200).json({ ...data, variants: hydratedVariants })
  } catch (error) {
    console.error('AI consultant cart-simple-batch render error:', error)
    recordAiConsultantDiagnostic('render.batch_proxy.error', {
      traceId,
      message: error instanceof Error ? error.message : String(error),
    })
    await recordAiConsultantTraceEvent(traceId, 'render.cart-simple-batch.error', {
      message: error instanceof Error ? error.message : String(error),
    })
    res.status(500).json({
      message: RENDER_REQUEST_FAILURE_MESSAGE,
    })
  }
}

export default handler
"""


def build_widget_patch() -> tuple[str, str, str]:
    rel = "src/components/ai-consultant/AIConsultantWidget.tsx"
    old = read(rel)
    new = old
    new = new.replace(
        """type ConsultantPendingRenderJob = {
  id: string
  jobId: string
  traceId?: string
  messageId?: string
  recommendationKind?: RecommendationKind
  groupIndex?: number
}

type ConsultantRecommendationJob = {""",
        """type ConsultantPendingRenderJob = {
  id: string
  jobId: string
  traceId?: string
  messageId?: string
  recommendationKind?: RecommendationKind
  groupIndex?: number
}

type RenderBatchImageResult = {
  variantIndex: number
  imageUrl?: string
}

type ConsultantRecommendationJob = {""",
    )
    old_poll = """const pollRenderJobImage = async (jobId: string, traceId?: string) => {
  const startedAt = Date.now()
  let attempt = 0

  while (Date.now() - startedAt < RENDER_POLL_TIMEOUT_MS) {
    await wait(attempt === 0 ? 1500 : 3000)
    attempt += 1

    try {
      const response = await fetchRenderJobStatus(jobId, traceId)

      if (!response.ok && response.status !== 409) {
        continue
      }

      const data = await response.json()
      const imageUrl = extractRenderImageUrl(data)

      if (imageUrl) {
        return imageUrl
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        console.warn('AI consultant render job polling timed out:', jobId)
      } else {
        console.error('AI consultant render job polling error:', error)
      }
    }
  }

  return undefined
}
"""
    batch_poll = old_poll + """
const getObjectRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null

const getBatchResultRows = (data: unknown) => {
  const root = getObjectRecord(data)
  const result = getObjectRecord(root?.result)
  const candidates = [result?.results, root?.results]

  for (const candidate of candidates) {
    if (Array.isArray(candidate)) {
      return candidate
    }
  }

  return []
}

const readZeroBasedVariantIndex = (
  row: Record<string, unknown>,
  fallbackIndex: number,
) => {
  const rawIndex = row.variant_index ?? row.variantIndex

  if (typeof rawIndex === 'number' && Number.isInteger(rawIndex) && rawIndex > 0) {
    return rawIndex - 1
  }

  if (typeof rawIndex === 'string') {
    const parsed = Number(rawIndex)

    if (Number.isInteger(parsed) && parsed > 0) {
      return parsed - 1
    }
  }

  return fallbackIndex
}

const extractRenderBatchImageUrls = (data: unknown): RenderBatchImageResult[] =>
  getBatchResultRows(data)
    .reduce<RenderBatchImageResult[]>((results, row, index) => {
      const record = getObjectRecord(row)

      if (!record) {
        return results
      }

      results.push({
        variantIndex: readZeroBasedVariantIndex(record, index),
        imageUrl: extractRenderImageUrl(record.render ?? record),
      })

      return results
    }, [])
    .sort((left, right) => left.variantIndex - right.variantIndex)

const pollRenderBatchJobImages = async (
  jobId: string,
  expectedCount: number,
  traceId?: string,
): Promise<RenderBatchImageResult[]> => {
  const startedAt = Date.now()
  let attempt = 0

  while (Date.now() - startedAt < RENDER_POLL_TIMEOUT_MS) {
    await wait(attempt === 0 ? 1500 : 3000)
    attempt += 1

    try {
      const response = await fetchRenderJobStatus(jobId, traceId)

      if (!response.ok && response.status !== 409) {
        continue
      }

      const data = await response.json()
      const rows = extractRenderBatchImageUrls(data)
      const status = getObjectRecord(data)?.status

      if (
        rows.some((row) => row.imageUrl) &&
        (rows.filter((row) => row.imageUrl).length >= expectedCount ||
          status === 'finished')
      ) {
        return rows
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        console.warn('AI consultant render batch job polling timed out:', jobId)
      } else {
        console.error('AI consultant render batch job polling error:', error)
      }
    }
  }

  return []
}
"""
    new = new.replace(old_poll, batch_poll)
    new = new.replace(
        """        const withoutDuplicate = pendingRenderJobs.filter(
          (job) =>
            job.jobId !== nextPendingRenderJob.jobId ||
            job.messageId !== nextPendingRenderJob.messageId,
        )
""",
        """        const withoutDuplicate = pendingRenderJobs.filter(
          (job) =>
            job.jobId !== nextPendingRenderJob.jobId ||
            job.messageId !== nextPendingRenderJob.messageId ||
            job.groupIndex !== nextPendingRenderJob.groupIndex,
        )
""",
    )
    old_resume = """      void Promise.all(
        pendingRenderBatch.map(async (pendingRenderJob) => {
          try {
            return {
              pendingRenderJob,
              renderImageUrl: await pollRenderJobImage(
                pendingRenderJob.jobId,
                pendingRenderJob.traceId,
              ),
            }
          } catch (error) {
            console.error('AI consultant resumed render batch item error:', error)
            return {
              pendingRenderJob,
              renderImageUrl: null,
            }
          }
        }),
      )
        .then(async (renderResults) => {
"""
    new_resume = """      const pollPendingRenderBatch = async () => {
        const uniqueJobIds = Array.from(
          new Set(pendingRenderBatch.map((pendingRenderJob) => pendingRenderJob.jobId)),
        )

        if (uniqueJobIds.length === 1 && pendingRenderBatch.length > 1) {
          const batchRows = await pollRenderBatchJobImages(
            uniqueJobIds[0],
            pendingRenderBatch.length,
            pendingRenderBatch[0]?.traceId,
          )
          const imageByIndex = new Map(
            batchRows.map((row) => [row.variantIndex, row.imageUrl]),
          )

          return pendingRenderBatch.map((pendingRenderJob, index) => ({
            pendingRenderJob,
            renderImageUrl:
              imageByIndex.get(pendingRenderJob.groupIndex ?? index) ?? null,
          }))
        }

        return Promise.all(
          pendingRenderBatch.map(async (pendingRenderJob) => {
            try {
              return {
                pendingRenderJob,
                renderImageUrl: await pollRenderJobImage(
                  pendingRenderJob.jobId,
                  pendingRenderJob.traceId,
                ),
              }
            } catch (error) {
              console.error('AI consultant resumed render batch item error:', error)
              return {
                pendingRenderJob,
                renderImageUrl: null,
              }
            }
          }),
        )
      }

      void pollPendingRenderBatch()
        .then(async (renderResults) => {
"""
    new = new.replace(old_resume, new_resume)
    old_render_block = """        const renderPromises = renderGroups.map((group, index) =>
          requestRenderImageForProducts(
            group,
            renderRequest,
            traceId,
            pendingRenderMessage.id,
            index,
          ).catch((error) => {
            console.error('AI consultant render group error:', error)
            return null
          }),
        )

        try {
          const renderResults = await Promise.all(renderPromises)

          for (let index = 0; index < renderResults.length; index += 1) {
            const renderImageUrl = renderResults[index]

            if (!isCurrentAsyncRun()) {
              return
            }

            await showSequencingPause()

            if (!isCurrentAsyncRun()) {
              return
            }

            if (renderImageUrl) {
              appendRenderResultMessage(
                renderImageUrl,
                traceId,
                getStylingRenderResultMessage(index),
                index < renderPromises.length - 1,
                recommendationGroupId,
                index,
              )
            } else {
              appendAssistantMessageToThread('?붿옄???앹꽦 ?붿껌???ㅽ뙣?덉뒿?덈떎', {
                recommendationKind: 'styling',
                recommendationGroupId,
                recommendationGroupIndex: index,
                diagnosticId: traceId,
                suppressFollowup: index < renderPromises.length - 1,
              })
            }
          }
        } finally {
"""
    new_render_block = """        const appendBatchRenderResults = async (
          renderResults: RenderBatchImageResult[],
        ) => {
          const imageByIndex = new Map(
            renderResults.map((row) => [row.variantIndex, row.imageUrl]),
          )

          for (let index = 0; index < renderGroups.length; index += 1) {
            const renderImageUrl = imageByIndex.get(index)

            if (!isCurrentAsyncRun()) {
              return
            }

            await showSequencingPause()

            if (!isCurrentAsyncRun()) {
              return
            }

            if (renderImageUrl) {
              appendRenderResultMessage(
                renderImageUrl,
                traceId,
                getStylingRenderResultMessage(index),
                index < renderGroups.length - 1,
                recommendationGroupId,
                index,
              )
            } else {
              appendAssistantMessageToThread('?붿옄???앹꽦 ?붿껌???ㅽ뙣?덉뒿?덈떎', {
                recommendationKind: 'styling',
                recommendationGroupId,
                recommendationGroupIndex: index,
                diagnosticId: traceId,
                suppressFollowup: index < renderGroups.length - 1,
              })
            }
          }
        }

        let pendingRenderJobs: ConsultantPendingRenderJob[] = []

        try {
          const response = await fetch('/api/ai-consultant/render/cart-simple-batch', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              imageUrl: renderRequest.imageUrl,
              variants: renderGroups.map((products) => ({ products })),
              traceId,
            }),
          })
          const data = await response.json().catch(() => ({}))

          if (!isCurrentAsyncRun()) {
            return
          }

          if (!response.ok) {
            throw new Error(
              typeof data.message === 'string'
                ? data.message
                : 'AI render batch request failed',
            )
          }

          const immediateResults = extractRenderBatchImageUrls(data)

          if (immediateResults.some((row) => row.imageUrl)) {
            await appendBatchRenderResults(immediateResults)
            return
          }

          const renderJobId = extractRenderJobId(data)

          if (!renderJobId) {
            await appendBatchRenderResults([])
            return
          }

          pendingRenderJobs = renderGroups.map((_, index) =>
            addPendingRenderJob(targetThreadId, {
              jobId: renderJobId,
              traceId,
              messageId: pendingRenderMessage.id,
              recommendationKind: 'styling',
              groupIndex: index,
            }),
          )

          const polledResults = await pollRenderBatchJobImages(
            renderJobId,
            renderGroups.length,
            traceId,
          )

          await appendBatchRenderResults(polledResults)
        } catch (error) {
          console.error('AI consultant render batch error:', error)
          await appendBatchRenderResults([])
        } finally {
          if (pendingRenderJobs.length > 0) {
            if (isCurrentAsyncRun()) {
              pendingRenderJobs.forEach((pendingRenderJob) =>
                removePendingRenderJob(targetThreadId, pendingRenderJob),
              )
            } else {
              pendingRenderJobs.forEach((pendingRenderJob) =>
                releasePendingRenderJobLock(targetThreadId, pendingRenderJob.jobId),
              )
            }
          }
"""
    new, render_block_count = re.subn(
        r"        const renderPromises = renderGroups\.map\(\(group, index\) =>\n"
        r".*?"
        r"        \} finally \{\n",
        new_render_block,
        new,
        count=1,
        flags=re.DOTALL,
    )
    if render_block_count != 1:
        raise RuntimeError(f"render block replacement failed: {render_block_count}")
    required = [
        "type RenderBatchImageResult",
        "pollRenderBatchJobImages",
        "job.groupIndex !== nextPendingRenderJob.groupIndex",
        "pollPendingRenderBatch",
        "'/api/ai-consultant/render/cart-simple-batch'",
    ]
    missing = [item for item in required if item not in new]
    if missing:
        raise RuntimeError(f"widget replacement failed: {missing}")
    if new == old:
        raise RuntimeError("widget unchanged")
    return rel, old, new


def main() -> None:
    render_rel, render_old, render_new = build_render_ts_patch()
    widget_rel, widget_old, widget_new = build_widget_patch()
    patch = ""
    patch += diff_modified(render_rel, render_old, render_new)
    patch += diff_new("src/pages/api/ai-consultant/render/cart-simple-batch.ts", NEW_BATCH_ROUTE)
    patch += diff_modified(widget_rel, widget_old, widget_new)
    PATCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    PATCH_PATH.write_text(patch, encoding="utf-8", newline="\n")
    print(PATCH_PATH)


if __name__ == "__main__":
    main()
