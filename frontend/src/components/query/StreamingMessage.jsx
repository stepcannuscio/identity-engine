export default function StreamingMessage({ message }) {
  return (
    <article className="message streaming">
      <div className="streaming-body">
        {message.error ? (
          <p className="streaming-error">{message.error}</p>
        ) : (
          <div className="streaming-copy">
            {message.content}
            <span className="streaming-cursor">|</span>
          </div>
        )}
      </div>
    </article>
  )
}
