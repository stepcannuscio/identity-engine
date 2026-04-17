import PrivacyStatus from '../privacy/PrivacyStatus.jsx'

export default function StreamingMessage({ message }) {
  const privacy = message.privacy ?? message.metadata?.privacy

  return (
    <article className="message streaming">
      <div className="streaming-body">
        <PrivacyStatus privacy={privacy} />
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
