"""
Audio representations, i.e. Wave, Spectrum, Spectrogram.  Should always inherit
from ndarray, but utility functions may be added, e.g. loading audio files,
playing or plotting

TODO:
    - Signal.__reduce__, new_state undefined ?
"""
from __future__ import division, print_function
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from ..utilities import plot, conversion
from scipy.io import wavfile
from ..base import types as _types
from ..base import defaults
from ..soundcard import audio_driver
from ..analysis import loudness


class Signal(np.ndarray):
    """
    Time domain signal. Layout is one column per channel.

    Parameters
    ----------

    samples: ndarray
        Signal data.
    sample_rate: int
        Sample rate in samples / second.
    """
    __array_priority__ = 10

    def __new__(cls, samples, sample_rate=defaults.sample_rate):

        if len(samples.shape) == 1:
            samples.shape = (samples.shape[0], 1)
        instance = np.array(samples).view(cls)  # Copies samples
        instance.sample_rate = sample_rate
        return instance

    def __array_finalize__(self, obj):
        if obj is None:
            return
        self.sample_rate = getattr(obj, 'sample_rate', defaults.sample_rate)

    def __array_prepare__(self, out_arr, context=None):
        return np.ndarray.__array_prepare__(self, out_arr, context)

    def __array_wrap__(self, out_arr, context=None):
        return np.ndarray.__array_wrap__(self, out_arr, context)

    def __reduce__(self):  # pickle additional attributes
        pickled_state = super(Signal, self).__reduce__()
        new_state = pickled_state[2] + (self.sample_rate,)
        return (pickled_state[0], pickled_state[1], new_state)

    def __setstate__(self, state):
        self.sample_rate = state[-1]
        super(Signal, self).__setstate__(state[0:-1])

    @property
    def num_channels(self):
        """
        Number of channels
        """
        return 1 if len(self.shape) == 1 else self.shape[1]

    @property
    def num_frames(self):
        """
        Number of frames (samples)
        """
        return self.shape[0]

    @property
    def duration(self):
        return float(self.num_frames) / self.sample_rate

    @property
    def time(self):
        return np.arange(self.num_frames) / self.sample_rate

    def check_mono(self):
        """
        Utility for ensuring the signal is mono (one channel)
        """
        if self.num_channels > 1:
            raise Exception("Unsupported channel layout")

    def as_ndarray(self):
        """
        Return the data as ndarray again
        """
        return np.array(self)

    def plot(self):
        """
        Plot the signal using matplotlib.
        """

        if self.num_channels == 1:
            f = plt.plot(self.time, self)
        else:
            f, axes = plt.subplots(self.num_channels, sharex=True)
            for ch in range(self.num_channels):
                axes[ch].plot(self.time, self[:, ch])
        plt.xlabel('Time (s)')
        return f


class Wave(Signal):
    """
    Audio waveform signal.

    Parameters
    ----------
    samples: ndarray
        Signal data.
    sample_rate: int
        Sample rate in samples / second.
    """

    def __init__(self, samples, sample_rate=defaults.sample_rate):
        self.stream = None
        super(Wave, self).__new__(Wave, samples, sample_rate)

    def __array_finalize__(self, obj):
        if obj is None:
            return
        self.stream = getattr(obj, 'stream', None)
        self.sample_rate = getattr(obj, 'sample_rate', None)

    @classmethod
    def tone(cls, freq=1000, phase=0,
             duration=1, sample_rate=defaults.sample_rate):
        num_samples = conversion.nearest_sample(duration, sample_rate)
        t = np.arange(num_samples) / sample_rate
        return cls(np.sin(2 * np.pi * t * freq + phase), sample_rate)

    @classmethod
    def read(cls, filename):
        """
        Read an audio file (only wav is supported).

        Parameters
        ----------
        filename: string
            Path to the wav file.
        """
        sample_rate, samples = wavfile.read(filename)
        if samples.dtype == np.dtype('int16'):
            samples = (samples.astype(_types.float_) /
                       np.iinfo(np.dtype('int16')).min)
        if len(samples.shape) == 1:
            samples = samples.reshape((-1, 1))
        instance = cls(samples, sample_rate)
        return instance

    def write(self, filename):
        """
        Write the data to an audio file (only wav is supported).

        Parameters
        ----------
        filename: string
            Path to the wav file.
        """

        if self.peak_level > 0:
            print('Warning: Peak level exceeds 0 dB FS')
        wavfile.write(filename, self.sample_rate, self)

    @property
    def left(self):
        return Wave(self[:, 0], self.sample_rate)

    @property
    def right(self):
        if self.num_channels > 1:
            return Wave(self[:, 1], self.sample_rate)
        else:
            raise AttributeError('Wave only has left channel')

    def with_duration(self, duration):

        frames = conversion.nearest_sample(duration,
                                           self.sample_rate)

        if self.num_frames < frames:
            return Wave(self.zero_pad(0, frames - self.num_frames),
                        self.sample_rate)

        elif self.num_frames > frames:
            return Wave(self[:frames], self.sample_rate)

        else:
            return Wave(self, self.sample_rate)

    def append(self, other):

        return Wave(np.r_[self, other], self.sample_rate)

    def __add__(self, other):

        if isinstance(other, self.__class__):

            max_frames = np.maximum(self.num_frames, other.num_frames)
            max_channels = np.maximum(self.num_channels, other.num_channels)

            result = Wave(np.zeros((max_frames, max_channels)),
                          self.sample_rate)

            result[:self.num_frames, :self.num_channels] = self
            result[:other.num_frames, :other.num_channels] += other

        else:
            result = Wave(np.add(self, other), self.sample_rate)

        return result

    @property
    def level(self):
        return conversion.power_to_db(
            np.mean(self.flatten() ** 2)
        )

    @level.setter
    def level(self, target_level):
        gain = conversion.db_to_amp(target_level - self.level)
        self *= gain

    @property
    def peak_level(self):
        return conversion.amp_to_db(
            np.max(np.abs(self))
        )

    @peak_level.setter
    def peak_level(self, target_level):
        gain = conversion.db_to_amp(target_level - self.peak_level)
        self *= gain

    @property
    def loudness(self):
        ebur128 = loudness.EBUR128(sample_rate=self.sample_rate)
        return ebur128.process(self).P

    @loudness.setter
    def loudness(self, target_loudness):
        gain = conversion.db_to_amp(target_loudness - self.loudness)
        self *= gain

    def normalize(self):
        """
        Normalize by maximum amplitude.
        """
        return Wave(np.divide(self, np.max(np.abs(self), 0)), self.sample_rate)

    def zero_pad(self, start_frames, end_frames=0):
        """
        Pad with zeros at the start and/or end

        Parameters
        ----------
        start_frames: int
            Number of zeros at the start.
        end_frames: int
            Number of zeros at the end.

        """

        start = np.zeros((start_frames, self.num_channels), _types.float_)
        end = np.zeros((end_frames, self.num_channels), _types.float_)
        # avoid 1d shape
        tmp = self.reshape(self.shape[0], self.num_channels)
        return Wave(np.concatenate((start, tmp, end)), self.sample_rate)

    def as_mono(self):
        return Wave(self.mean(1).reshape(-1, 1),
                    self.sample_rate)

    def as_stereo(self):
        if self.num_channels == 1:
            return Wave(np.tile(self, 2), self.sample_rate)
        else:
            return Wave(self[:, :2], self.sample_rate)

    def play(self, stop_func=None):
        """
        Play the sound with the current audio driver.

        Parameters
        ----------
        stop_func: function
            Function to execute when the sound ends.
        """
        if audio_driver is not None and self.stream is None:
            self.stream = audio_driver.play(
                self, sr=self.sample_rate, stop_func=stop_func
            )

    def stop(self):
        """
        Stop playback if playing.
        """
        if audio_driver is not None:
            audio_driver.stop(self.stream)
            self.stream = None

    @classmethod
    def record(cls, max_seconds=10, num_channels=2,
               sr=defaults.sample_rate, stop_func=None):
        return audio_driver.record(max_seconds, num_channels, sr, stop_func)


class Spectrum(Signal):
    """
    Audio spectrum
    """

    def __new__(cls, samples, sample_rate, freqs=None):

        instance = Signal.__new__(cls, samples, sample_rate)
        instance.freqs = freqs

        return instance

    def __array_finalize__(self, obj):
        if obj is None:
            return
        self.freqs = getattr(obj, 'freqs', None)

    def magnitude(self):
        """
        Return the magnitude spectrum.
        """
        return np.abs(self)

    def phase(self):
        """
        Return the phase spectrum.
        """
        return np.angle(self)

    def plot_magnitude(self, log_mag=True, log_x=True):

        mag = self.magnitude()

        y_label = 'Magnitude'
        if log_mag:
            mag = conversion.amp_to_db(mag)
            y_label += ', dB'

        axes = plt.gca()

        if log_x:
            axes.semilogx(self.freqs, mag)
        else:
            axes.plot(self.freqs, mag)

        axes.set_xlabel('Frequency, Hz')
        axes.set_ylabel(y_label)

        return axes


class Spectrogram(Signal):
    """

    This class represents a general time-frequency representation of a signal.
    It is not necessarily tied to STFT, e.g. the output of the Gammatone
    processor is a Spectrogam instance. It is basically a matrix of an abitrary
    data type, where rows represent frequency and columns represent time.

    Parameters
    ----------
    samples: float, complex
    sample_rate: int
        Sample rate in samples / second of the original time domain signal.
    hop_size: int
         Hop size of the time-frequency transform (default is 1).
    freqs: float
        Centre frequencies of the filters used for the time-frequency
        transform. If None, frequencies are assumed to be linearly spaced and
        are determined from the shape of the input array.
    """

    def __new__(cls, samples, sample_rate=defaults.sample_rate,
                hop_size=1, freqs=None):
        instance = Signal.__new__(cls, samples, sample_rate)
        instance.hop_size = hop_size
        instance.freqs = freqs
        return instance

    def __array_finalize__(self, obj):
        if obj is None:
            return
        self.sample_rate = getattr(obj, 'sample_rate', defaults.sample_rate)
        self.hop_size = getattr(obj, 'hop_size', None)
        self.freqs = getattr(obj, 'freqs', None)

    @property
    def num_channels(self):
        return 1

    @property
    def num_bands(self):
        return self.shape[0]

    @property
    def num_frames(self):
        return self.shape[1]

    @property
    def duration(self):
        return float(self.num_frames * self.hop_size) / self.sample_rate

    @property
    def time(self):
        hop_secs = float(self.hop_size) / self.sample_rate
        return np.arange(self.num_frames) * hop_secs

    def magnitude(self):
        return np.abs(self)

    def phase(self):
        return np.angle(self)

    def zero_pad(self, start_frames, end_frames=0):
        """
        Pad with zeros at the start and/or end

        Parameters
        ----------
        start_frames: int
            Number of zeros at the start.
        end_frames: int
            Number of zeros at the end.

        """

        start = np.zeros((self.num_bands, start_frames), _types.float_)
        end = np.zeros((self.num_bands, end_frames), _types.float_)
        return Spectrogram(
                np.c_[start, self, end],
                self.sample_rate,
                self.hop_size,
                self.freqs)

    def plot(self, **kwargs):
        return self.plot_magnitude(**kwargs)

    def plot_magnitude(self, colormap="CMRmap", min_freq=None, max_freq=None,
                       axes=None, label_x=True, label_y=True, title=None,
                       colorbar=True, log_mag=True, log_y=False):
        """
        Plot the magnitude spectrogram

        Parameters
        ----------
        colormap: string
            Matplotlib colormap.
        min_freq: float
            minimum frequency in Hz (for labelling the axis).
        max_freq: float
            maximum frequency in Hz (for labelling the axis).
        axes: matplotlib axes object
            Axes object for plotting on existing figure.
        label_x: boolean
            Add labels to x axis.
        label_y: boolean
            Add labels to y axis.
        title: string
            Plot title (overlaid on image).
        colorbar: boolean
            Add a colorbar.
        log_mag: boolean
            Plot log magnitude.
        log_y: boolean
            Plot on log-y axis.
        """
        mag = self.magnitude()
        if log_mag:
            mag = 20. * np.log10((mag / np.max(mag)) + np.spacing(1))
            min_val = -80
        else:
            min_val = 0

        if axes is None:
            axes = plt.gca()

        if max_freq is None:
            max_freq = self.freqs[-1]

        if min_freq is None:
            min_freq = self.freqs[0]

        img = axes.imshow(
            mag,
            cmap=colormap,
            aspect="auto",
            vmin=min_val,
            origin="low",
            interpolation='bilinear',
            extent=[0, self.time[-1], min_freq, max_freq],
        )

        if colorbar:
            plt.colorbar(img, ax=axes)
        if label_x:
            axes.set_xlabel("Time (s)")
        if label_y:
            axes.set_ylabel("Frequency (Hz)")

        if log_y:
            axes.set_yscale('symlog')

        ytick_labels = plot.nice_hertz_labels(axes.get_yticks())
        axes.set_yticklabels(ytick_labels)
        plt.setp(axes.get_xticklabels(), visible=label_x)
        plt.setp(axes.get_yticklabels(), visible=label_y)

        if title is not None:
            axes.text(0.9, 0.9, title, horizontalalignment='right',
                      bbox={'facecolor': 'white', 'alpha': 0.7, 'pad': 5},
                      transform=axes.transAxes)
        return axes


class TFMask(Spectrogram):
    """
    Base time-frequency mask for multiplying with spectrograms.
    """

    def plot(self, mask_color=(1, 0, 0, 0.5), min_freq=0, max_freq=None,
             axes=None, label_x=True, label_y=True, title=None):
        """
        Plot the time-frequency mask.

        Parameters
        ----------
        mask_color: tuple
            Color specification (including alpha) for the mask
        min_freq: float
            minimum frequency in Hz (for labelling the axis).
        max_freq: float
            maximum frequency in Hz (for labelling the axis).
        axes: matplotlib axes object
            Axes object for plotting on existing figure.
            If provided, the mask is assumed to be overlaif on a spectrogram.
        label_x: boolean
            Add labels to x axis.
        label_y: boolean
            Add labels to y axis.
        title: string
            Plot title (overlaid on image).
        """
        if axes is None:
            colormap = LinearSegmentedColormap.from_list(
                "map", ["white", "black"])
        else:
            alpha_color = [mask_color[0], mask_color[1], mask_color[2], 0]
            colormap = LinearSegmentedColormap.from_list(
                "map", [alpha_color, mask_color])
        Spectrogram.plot_magnitude(
            self, colormap, min_freq, max_freq, axes, label_x, label_y, title,
            False, False
        )


class BinaryMask(TFMask):
    """
    Binary Mask based on a comparison between target and background.
    If the threshold is 0, the mask is 1 when the target magnitude is larger
    than the background, and 0 otherwise.
    """

    def __new__(cls, target, background, threshold=0):
        tm = target.magnitude() + defaults.eps
        bm = background.magnitude() + defaults.eps
        mask = (20 * np.log10(tm / bm) > threshold).astype(_types.float_)
        instance = TFMask.__new__(cls, mask)
        instance.sample_rate = target.sample_rate
        instance.hop_size = target.hop_size
        return instance


class RatioMask(TFMask):
    """
    Ratio Mask: soft mask based on ratio of target to background magnitude,
    with optional exponent p.
    """

    def __new__(cls, target, background, p=1):
        tm = target.magnitude() + defaults.eps
        bm = background.magnitude() + defaults.eps
        mask = (tm**p / (tm + bm)**p).astype(_types.float_)
        instance = TFMask.__new__(cls, mask)
        instance.sample_rate = target.sample_rate
        instance.hop_size = target.hop_size
        return instance


class ComplexRatioMask(TFMask):
    """
    Complex Ratio Mask: mask based on the ratio of the (complex) target and
    mixture.
    Williamson, D. Wang, Y., and Wang, D. Complex Ratio Masking for Monaural
    Speech Seperation. IEEE/ACM transactions on audio, speech, and language
    processing. Vol 24, No. 3. 2016.
    """

    def __new__(cls, target, background):
        eps = complex(defaults.eps, defaults.eps)
        mask = ((target + eps) /
                (target + background + eps))
        instance = TFMask.__new__(cls, mask)
        instance.sample_rate = target.sample_rate
        instance.hop_size = target.hop_size
        return instance

    def compress(self, k=10, c=0.1):
        exp = np.exp(-c * self)
        return k * (1 - exp) / (1 + exp)

    def uncompress(self, k=10, c=0.1):
        return -1/c * np.log((k - self) / (k + self))
