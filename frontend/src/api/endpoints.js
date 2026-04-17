import client from './client.js'

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

export const capturePreview = async (text, domainHint) => {
  const { data } = await client.post('/capture/preview', {
    text,
    domain_hint: domainHint || null,
  })
  return data
}

export const capture = async (text, domainHint, accepted) => {
  const { data } = await client.post('/capture', {
    text,
    domain_hint: domainHint || null,
    accepted: accepted ?? null,
  })
  return data
}

export const createPreferenceSignal = async (payload) => {
  const { data } = await client.post('/preferences/signals', payload)
  return data
}

export const previewInterview = async (domain, question, answer) => {
  const { data } = await client.post('/interview/preview', {
    domain,
    question,
    answer,
  })
  return data
}

export const saveInterview = async (domain, question, answer, accepted) => {
  const { data } = await client.post('/interview', {
    domain,
    question,
    answer,
    accepted: accepted ?? null,
  })
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
    const { data } = await client.post('/artifacts', formData)
    return data
  }

  const { data } = await client.post('/artifacts', {
    text,
    title: title || null,
    type: type || null,
    source: source || null,
    domain: domain || null,
    metadata: metadata ?? null,
  })
  return data
}

export const getSessions = async () => {
  const { data } = await client.get('/sessions')
  return data
}

export const getCurrentSession = async () => {
  const { data } = await client.get('/sessions/current')
  return data
}
