import client, { withSlowRequestTimeout } from './client.js'

export const login = async (passphrase) => {
  const { data } = await client.post('/auth/login', { passphrase })
  return data
}

export const logout = async () => {
  const { data } = await client.post('/auth/logout')
  return data
}

export const getAuthStatus = async () => {
  const { data } = await client.get('/auth/status')
  return data
}

export const getAttributes = async (domain) => {
  const { data } = await client.get('/attributes', {
    params: domain ? { domain } : undefined,
  })
  return data
}

export const getAttribute = async (id) => {
  const { data } = await client.get(`/attributes/${id}`)
  return data
}

export const updateAttribute = async (id, payload) => {
  const { data } = await client.put(`/attributes/${id}`, payload)
  return data
}

export const correctAttribute = async (id, payload) => {
  const { data } = await client.patch(`/attributes/${id}`, payload)
  return data
}

export const retractAttribute = async (id) => {
  const { data } = await client.delete(`/attributes/${id}`)
  return data
}

export const createAttribute = async (payload) => {
  const { data } = await client.post('/attributes', payload)
  return data
}

export const confirmAttribute = async (id) => {
  const { data } = await client.post(`/attributes/${id}/confirm`)
  return data
}

export const getDomains = async () => {
  const { data } = await client.get('/domains')
  return data
}

export const capturePreview = async (text, domainHint, allowExternalExtraction = false) => {
  const { data } = await client.post(
    '/capture/preview',
    {
      text,
      domain_hint: domainHint || null,
      allow_external_extraction: allowExternalExtraction,
    },
    withSlowRequestTimeout(),
  )
  return data
}

export const capture = async (
  text,
  domainHint,
  accepted,
  allowExternalExtraction = false,
) => {
  const { data } = await client.post(
    '/capture',
    {
      text,
      domain_hint: domainHint || null,
      accepted: accepted ?? null,
      allow_external_extraction: allowExternalExtraction,
    },
    withSlowRequestTimeout(),
  )
  return data
}

export const createPreferenceSignal = async (payload) => {
  const { data } = await client.post('/preferences/signals', payload)
  return data
}

export const previewInterview = async (
  domain,
  question,
  answer,
  allowExternalExtraction = false,
) => {
  const { data } = await client.post(
    '/interview/preview',
    {
      domain,
      question,
      answer,
      allow_external_extraction: allowExternalExtraction,
    },
    withSlowRequestTimeout(),
  )
  return data
}

export const saveInterview = async (
  domain,
  question,
  answer,
  accepted,
  allowExternalExtraction = false,
) => {
  const { data } = await client.post(
    '/interview',
    {
      domain,
      question,
      answer,
      accepted: accepted ?? null,
      allow_external_extraction: allowExternalExtraction,
    },
    withSlowRequestTimeout(),
  )
  return data
}

export const uploadArtifact = async ({
  text,
  file,
  title,
  type,
  source,
  domain,
  metadata,
  tags,
}) => {
  if (file) {
    const formData = new FormData()
    formData.append('file', file)
    if (title) {
      formData.append('title', title)
    }
    if (type) {
      formData.append('type', type)
    }
    if (source) {
      formData.append('source', source)
    }
    if (domain) {
      formData.append('domain', domain)
    }
    if (metadata) {
      formData.append('metadata', JSON.stringify(metadata))
    }
    if (tags?.length) {
      formData.append('tags', JSON.stringify(tags))
    }
    const { data } = await client.post('/artifacts', formData, withSlowRequestTimeout())
    return data
  }

  const { data } = await client.post(
    '/artifacts',
    {
      text,
      title: title || null,
      type: type || null,
      source: source || null,
      domain: domain || null,
      metadata: metadata ?? null,
      tags: tags ?? [],
    },
    withSlowRequestTimeout(),
  )
  return data
}

export const analyzeArtifact = async (artifactId) => {
  const { data } = await client.post(
    `/artifacts/${artifactId}/analyze`,
    null,
    withSlowRequestTimeout(),
  )
  return data
}

export const getArtifactAnalysis = async (artifactId) => {
  const { data } = await client.get(`/artifacts/${artifactId}/analysis`)
  return data
}

export const promoteArtifact = async (artifactId, payload) => {
  const { data } = await client.post(
    `/artifacts/${artifactId}/promote`,
    payload,
    withSlowRequestTimeout(),
  )
  return data
}

export const getSessions = async () => {
  const { data } = await client.get('/sessions')
  return data
}

export const submitQueryFeedback = async (payload) => {
  const { data } = await client.post('/query/feedback', payload)
  return data
}

export const getCurrentSession = async () => {
  const { data } = await client.get('/sessions/current')
  return data
}

export const getTeachBootstrap = async () => {
  const { data } = await client.get('/teach/bootstrap')
  return data
}

export const getTeachQuestions = async () => {
  const { data } = await client.get('/teach/questions')
  return data
}

export const answerTeachQuestion = async (questionId, payload) => {
  const { data } = await client.post(
    `/teach/questions/${questionId}/answer`,
    payload,
    withSlowRequestTimeout(),
  )
  return data
}

export const feedbackTeachQuestion = async (questionId, feedback) => {
  const { data } = await client.post(`/teach/questions/${questionId}/feedback`, { feedback })
  return data
}

export const getSetupModelOptions = async () => {
  const { data } = await client.get('/setup/model-options')
  return data
}

export const saveProviderCredentials = async (provider, credentials) => {
  const { data } = await client.post(`/setup/providers/${provider}/credentials`, {
    credentials,
  })
  return data
}

export const saveSetupProfile = async (payload) => {
  const { data } = await client.post('/setup/profile', payload)
  return data
}

export const updateSecurityCheckOverride = async (checkCode, payload) => {
  const { data } = await client.post(`/setup/security-posture/checks/${checkCode}`, payload)
  return data
}

export const getSecurityPosture = async () => {
  const { data } = await client.get('/setup/security-posture')
  return data
}
