/**
 * La forma de onda reducida a `bucketCount` picos entre 0 y 1: el mayor valor absoluto de
 * cualquier canal en cada tramo, normalizado al tramo más fuerte para que un audio bajo
 * también se vea. Un audio en silencio da ceros. O(muestras · canales).
 */
export function waveformPeaks(channels: readonly Float32Array[], bucketCount: number): Float32Array {
  const length = channels.reduce((longest, channel) => Math.max(longest, channel.length), 0);
  const buckets = Math.max(0, Math.min(Math.floor(bucketCount), length));
  const peaks = new Float32Array(buckets);
  if (buckets === 0) {
    return peaks;
  }
  const samplesPerBucket = length / buckets;
  for (let bucket = 0; bucket < buckets; bucket += 1) {
    const from = Math.floor(bucket * samplesPerBucket);
    const to = Math.floor((bucket + 1) * samplesPerBucket);
    peaks[bucket] = loudestSample(channels, from, to);
  }
  return normalised(peaks);
}

function loudestSample(channels: readonly Float32Array[], from: number, to: number): number {
  let loudest = 0;
  for (const channel of channels) {
    const end = Math.min(to, channel.length);
    for (let index = from; index < end; index += 1) {
      const magnitude = Math.abs(channel[index] ?? 0);
      if (magnitude > loudest) {
        loudest = magnitude;
      }
    }
  }
  return loudest;
}

function normalised(peaks: Float32Array): Float32Array {
  const loudest = peaks.reduce((max, peak) => Math.max(max, peak), 0);
  if (loudest === 0) {
    return peaks;
  }
  return peaks.map((peak) => peak / loudest);
}

/** Los picos agrupados en `count` barras, cada una el mayor de su grupo: para un ancho menor. */
export function reducePeaks(peaks: Float32Array, count: number): Float32Array {
  const bars = Math.max(1, Math.min(Math.floor(count), peaks.length));
  if (bars === peaks.length) {
    return peaks;
  }
  const reduced = new Float32Array(bars);
  const perBar = peaks.length / bars;
  for (let bar = 0; bar < bars; bar += 1) {
    const group = peaks.subarray(Math.floor(bar * perBar), Math.max(Math.floor(bar * perBar) + 1, Math.floor((bar + 1) * perBar)));
    reduced[bar] = group.reduce((max, peak) => Math.max(max, peak), 0);
  }
  return reduced;
}
