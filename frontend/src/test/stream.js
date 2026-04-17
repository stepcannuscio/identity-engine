function serializeEvent(event) {
  return `data: ${JSON.stringify(event)}\n\n`
}

export function createSseStream(events, options = {}) {
  const { split = false } = options
  const encoder = new TextEncoder()

  return new ReadableStream({
    start(controller) {
      for (const event of events) {
        const block = serializeEvent(event)

        if (split && block.length > 2) {
          const midpoint = Math.floor(block.length / 2)
          controller.enqueue(encoder.encode(block.slice(0, midpoint)))
          controller.enqueue(encoder.encode(block.slice(midpoint)))
          continue
        }

        controller.enqueue(encoder.encode(block))
      }

      controller.close()
    },
  })
}

export function createStreamResponse(events, options = {}) {
  return new Response(createSseStream(events, options), {
    status: options.status ?? 200,
    headers: {
      'Content-Type': 'text/event-stream',
    },
  })
}

export function createJsonErrorResponse(status, payload, statusText = 'Bad Request') {
  return new Response(JSON.stringify(payload), {
    status,
    statusText,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}
