import os
import re
from typing import Union, Tuple, Optional
import numpy as np
import pandas as pd 
import matplotlib.pyplot as plt

from ephys.util import finder

def read_analog(
    filename: str,
    dtype: str = 'int16',
    channel_idx: Optional[np.ndarray] = None,
    sample_range: Optional[Tuple[int, int]] = None
) -> np.ndarray:
    """
    Read binary data from a SpikeGLX file and convert to microvoltage data.

    Parameters:
    -----------
    filename : str
        Path to the meta file or binary file.
    dtype : str, optional
        Data type of the binary file (default is 'int16').
    channel_idx : array-like or None, optional
        Indices of channels to read. If None, all channels are read (default is None).
    sample_range : tuple of int or None, optional
        Range of samples to read, specified as (start, end). If None, all samples are read (default is None).

    Returns:
    --------
    numpy.ndarray
        Array containing the read data, with shape (n_samples, n_channels).

    Raises:
    -------
    ValueError
        If the filename does not end with '.ap.bin' or '.ap.meta'.
    FileNotFoundError
        If either the binary or meta file does not exist.
    """
    # Ensure we have both .bin and .meta filenames
    if filename.endswith('.meta'):
        bin_fn = filename.replace('.meta', '.bin')
        meta_fn = filename
    elif filename.endswith('.bin'):
        bin_fn = filename
        meta_fn = filename.replace('.bin', '.meta')
    else:
        raise ValueError("Filename must end with either '.meta' or '.bin'")

    # Check if files exist
    if not os.path.exists(bin_fn):
        raise FileNotFoundError(f"The binary file {bin_fn} does not exist.")
    if not os.path.exists(meta_fn):
        raise FileNotFoundError(f"The meta file {meta_fn} does not exist.")

    meta = read_meta(meta_fn)
    n_channel = meta['nSavedChans']

    if channel_idx is None:
        channel_idx = get_channel_idx(meta)

    uV_per_bit = get_uV_per_bit(meta)
    data = read_bin(bin_fn, n_channel, dtype, channel_idx, sample_range)

    return data * uV_per_bit[channel_idx][np.newaxis, :]


def read_digital(filename, dtype='uint16'):
    """
    Read digital data from a SpikeGLX file.

    Parameters:
    -----------
    filename : str
        Path to the meta file or binary file.
    dtype : str, optional
        Data type of the binary file (default is 'uint16').

    Returns:
    --------
    pandas.DataFrame
        DataFrame containing event data with columns:
        - 'time': Timestamps of events in seconds
        - 'time_frame': Timestamps of events in frames
        - 'chan': Channel IDs of events
        - 'type': Event types (0: offset, 1: onset)
    """
    # Ensure we have both .bin and .meta filenames
    if filename.endswith('.meta'):
        bin_fn = filename.replace('.meta', '.bin')
        meta_fn = filename
    elif filename.endswith('.bin'):
        bin_fn = filename
        meta_fn = filename.replace('.bin', '.meta')
    else:
        raise ValueError("Filename must end with either '.meta' or '.bin'")

    # Check if files exist
    if not os.path.exists(bin_fn):
        raise FileNotFoundError(f"The binary file {bin_fn} does not exist.")
    if not os.path.exists(meta_fn):
        raise FileNotFoundError(f"The meta file {meta_fn} does not exist.")

    # Read the meta data
    meta = read_meta(meta_fn)
    n_channel = meta['nSavedChans']
    sample_rate = meta.get('imSampRate') or meta.get('niSampRate')
    channel_idx = get_channel_idx(meta, analog=False)

    # Read the digital data
    data = read_bin(bin_fn, n_channel=n_channel, dtype=dtype, channel_idx=channel_idx)
    n_digital = data.shape[1]
    data_bits = np.unpackbits(data.view('uint8'), bitorder='little').reshape(-1, n_digital * np.dtype(dtype).itemsize * 8)
    changes = np.where(np.diff(data_bits, axis=0) != 0)

    timestamps = changes[0] + 1
    event_id = changes[1]
    event_type = data_bits[changes[0] + 1, changes[1]]  # 0: offset, 1: onset
    
    events = pd.DataFrame({
        'time': timestamps / sample_rate,  # in seconds
        'sample': timestamps,
        'chan': event_id,
        'type': event_type
    })

    return events


def read_bin(filename: str, n_channel: int = 385, dtype: str = 'int16',
             channel_idx: Optional[Union[slice, np.ndarray]] = None,
             sample_range: Optional[Tuple[int, int]] = None) -> np.ndarray:
    """
    Read binary data from a SpikeGLX file.

    Parameters
    ----------
    filename : str
        Path to the binary file.
    n_channel : int, optional
        Number of channels in the recording (default is 385).
    dtype : str, optional
        Data type of the binary file (default is 'int16').
    channel_idx : array-like or slice, optional
        Indices of channels to read. If None, channels 0-383 are read.
    sample_range : tuple of int, optional
        Range of samples to read, specified as (start, end). If None, all samples are read.

    Returns
    -------
    numpy.ndarray
        Array containing the read data, with shape (n_sample, len(channel_idx)).

    Raises
    ------
    FileNotFoundError
        If the specified file does not exist.
    """
    
    if not os.path.exists(filename):
        raise FileNotFoundError(f"The file {filename} does not exist.")

    if channel_idx is None:
        channel_idx = slice(0, n_channel)  # Default to reading channels 0-383

    n_sample_file = os.path.getsize(filename) // (n_channel * np.dtype(dtype).itemsize)
    if sample_range is None:
        sample_range = (0, n_sample_file)

    offset = sample_range[0] * n_channel * np.dtype(dtype).itemsize
    n_sample = sample_range[1] - sample_range[0]
    
    data = np.memmap(filename, dtype=dtype, mode='r', shape=(n_sample, n_channel), offset=offset)
    return np.ascontiguousarray(data[:, channel_idx])


def read_meta(filename):
    """
    Read SpikeGLX meta data from a file.

    Parameters:
    -----------
    filename : str
        Path to the meta file.

    Returns:
    --------
    dict
        Dictionary containing the meta data with format:
        variable_name=int / float / str / list / tuple
    """
    filename_meta = filename.replace('.bin', '.meta')
    meta = {}
    with open(filename_meta, 'r') as f:
        for line in f:
            key, value = line.strip().split('=', 1)
            if key.startswith('~'):
                key = key[1:]  # Remove leading '~'
            
            # Parse complex structures
            if key == 'imroTbl':
                meta[key] = parse_imrotbl(value)
            elif key == 'snsChanMap':
                meta[key] = parse_snschanmap(value)
            elif key == 'snsGeomMap':
                meta[key] = parse_snsgeommap(value)
            elif key in ['acqMnMaXaDw', 'snsMnMaXaDw']:
                values = list(map(int, value.split(',')))
                meta[key] = {
                    'MN': values[0],  # Multiplexed Neural
                    'MA': values[1],  # Multiplexed Auxiliary
                    'XA': values[2],  # Auxiliary Analog
                    'DW': values[3]   # Digital Word
                }
            elif key == 'snsApLfSy':
                values = list(map(int, value.split(',')))
                meta[key] = {
                    'AP': values[0],
                    'LF': values[1],
                    'SY': values[2]
                }
            else:
                # Try to convert to int or float
                try:
                    meta[key] = int(value)
                except ValueError:
                    try:
                        meta[key] = float(value)
                    except ValueError:
                        # If not int or float, keep as string
                        meta[key] = value
    return meta


def parse_imrotbl(value):
    # Parse the Imec Readout Table (imRo)
    entries = re.findall(r'\((.*?)\)', value)
    
    # Parse header
    probe_type, num_channels = map(int, entries[0].split(','))

    channels = []
    for entry in entries[1:]:
        channel_data = tuple(map(int, entry.split()))
        
        if probe_type in [0, 1020, 1030, 1100, 1120, 1121, 1122, 1123, 1200, 1300]:  # NP 1.0-like
            if len(channel_data) == 6:
                channels.append({
                    'channel': channel_data[0],  # Channel ID
                    'bank': channel_data[1],     # Bank number of the connected electrode
                    'refid': channel_data[2],    # Reference ID index (0=ext, 1=tip, [2..4]=on-shnk-ref)
                    'apgain': channel_data[3],   # AP band gain
                    'lfgain': channel_data[4],   # LF band gain
                    'apfilt': channel_data[5]    # AP hipass filter applied (1=ON)
                })
                # Note: On-shank ref electrodes are {192,576,960}
        elif probe_type in [21, 2003, 2004]:  # NP 2.0, single multiplexed shank
            if len(channel_data) == 4:
                channels.append({
                    'channel': channel_data[0],    # Channel ID
                    'bank_mask': channel_data[1],  # Bank mask (logical OR of {1=bnk-0, 2=bnk-1, 4=bnk-2, 8=bnk-3})
                    'refid': channel_data[2],      # Reference ID index
                    'electrode': channel_data[3],  # Electrode ID (range [0,1279])
                    'apgain': 80
                })
                # Note for Type-21: Reference ID values are {0=ext, 1=tip, [2..5]=on-shnk-ref}
                # On-shank ref electrodes are {127,507,887,1251}
                # Note for Type-2003,2004: Reference ID values are {0=ext, 1=gnd, 2=tip}
                # On-shank reference electrodes are removed from commercial 2B probes
        elif probe_type in [24, 2013, 2014]:  # NP 2.0, 4-shank
            if len(channel_data) == 5:
                channels.append({
                    'channel': channel_data[0],   # Channel ID
                    'shank': channel_data[1],     # Shank ID (with tips pointing down, shank-0 is left-most)
                    'bank': channel_data[2],      # Bank ID
                    'refid': channel_data[3],     # Reference ID index
                    'electrode': channel_data[4], # Electrode ID (range [0,1279] on each shank)
                    'apgain': 80
                })
            # Note for Type-24: Reference ID values are {0=ext, [1..4]=tip[0..3], [5..8]=on-shnk-0, [9..12]=on-shnk-1, [13..16]=on-shnk-2, [17..20]=on-shnk-3}
            # On-shank ref electrodes of any shank are {127,511,895,1279}
            # Note for Type-2013,2014: Reference ID values are {0=ext, 1=gnd, [2..5]=tip[0..3]}
            # On-shank reference electrodes are removed from commercial 2B probes

    return {'probe_type': probe_type, 'num_channels': num_channels, 'channels': channels}


def parse_snschanmap(value):
    # Parse header
    header_match = re.match(r'\((\d+),(\d+),(\d+),(\d+),(\d+)\)', value)
    imec_header_match = re.match(r'\((\d+),(\d+),(\d+)\)', value)
    
    if header_match:
        header = {
            'MN_channels': int(header_match.group(1)),
            'MA_channels': int(header_match.group(2)),
            'mux_channels': int(header_match.group(3)),
            'XA_channels': int(header_match.group(4)),
            'XD_words': int(header_match.group(5))
        }
    elif imec_header_match:
        header = {
            'AP_channels': int(imec_header_match.group(1)),
            'LF_channels': int(imec_header_match.group(2)),
            'SY_channels': int(imec_header_match.group(3))
        }
    else:
        raise ValueError("Invalid header format in snsChanMap")
    
    # Parse channel map
    channel_map = []
    channel_pattern = r'\(([^;]+);(\d+):(\d+)\)'
    for match in re.finditer(channel_pattern, value):
        channel_map.append({
            'name': match.group(1),
            'channel': int(match.group(2)),
            'order': int(match.group(3))
        })
    
    return {
        'header': header,
        'channel_map': channel_map
    }


def parse_snsgeommap(value):
    """
    Parse the snsGeomMap value for imec probes.
    
    The GeomMap describes how electrodes are arranged on the probe.
    It consists of a header and electrode entries.
    
    Header format: (part_number,shank_count,shank_spacing,per_shank_width)
    Electrode entry format: (s:x:z:u) where:
        s: zero-based shank number (left-most when tips point down)
        x: x-coordinate (um) of electrode center
        z: z-coordinate (um) of electrode center
        u: 0/1 flag indicating if the electrode is "used"
    
    Note: (X,Z) coordinates are relative to each shank's own origin.
    X-origin is the left edge of the shank, Z-origin is the center of the bottom-most electrode row.
    """
    pattern = r'\(([^)]+)\)'
    matches = re.findall(pattern, value)
    
    if not matches:
        raise ValueError("Invalid snsGeomMap format")
    
    # Parse header
    header = matches[0].split(',')
    
    # Parse electrode entries
    geom_data = []
    for entry in matches[1:]:
        s, x, z, u = map(int, entry.split(':'))
        geom_data.append({
            'shank': s,
            'x': x,
            'z': z,
            'used': bool(u)
        })
    
    return {
        'header': {
            'part_number': header[0],
            'shank_count': int(header[1]),
            'shank_spacing': int(header[2]),
            'per_shank_width': int(header[3])
        },
        'electrodes': geom_data
    }


def get_gain(meta):
    """
    Get the gain of the recording.

    Parameters:
    -----------
    meta : dict
        Metadata dictionary containing recording information.

    Returns:
    --------
    numpy.ndarray
        Array of gain values for each channel.

    Raises:
    -------
    ValueError
        If the recording type is not recognized or if required metadata is missing.
    """
    if meta['typeThis'] == 'imec':
        if 'imroTbl' not in meta or 'channels' not in meta['imroTbl']:
            raise ValueError("Missing 'imroTbl' or 'channels' in metadata for imec recording")
        
        if meta['fileName'].endswith('.ap.bin'):
            return np.array([channel['apgain'] for channel in meta['imroTbl']['channels']])
        elif meta['fileName'].endswith('.lf.bin'):
            return np.array([channel['lfgain'] for channel in meta['imroTbl']['channels']])
        else:
            raise ValueError(f"Unrecognized file type for imec recording: {meta['filename']}")
    
    elif meta['typeThis'] == 'nidq':
        if 'snsMnMaXaDw' not in meta or 'niMNGain' not in meta or 'niMAGain' not in meta:
            raise ValueError("Missing required metadata for nidq recording")
        
        n_mn = meta['snsMnMaXaDw']['MN']
        n_ma = meta['snsMnMaXaDw']['MA']
        n_xa = meta['snsMnMaXaDw']['XA']
        n_dw = meta['snsMnMaXaDw']['DW']
        
        gains = np.ones(n_mn + n_ma + n_xa + n_dw)
        gains[:n_mn] = meta['niMNGain']
        gains[n_mn:n_mn + n_ma] = meta['niMAGain']
        return gains
    
    else:
        raise ValueError(f"Unrecognized recording type: {meta['typeThis']}")


def get_uV_per_bit(meta):
    AiRangeMax = meta.get('imAiRangeMax') or meta.get('niAiRangeMax')
    MaxInt = meta.get('imMaxInt') or meta.get('niMaxInt') or 512
    gains = get_gain(meta)
    return 1000000 * AiRangeMax / MaxInt / gains


def get_channel_idx(meta, analog=True):
    """
    Get the channel index slice based on the recording type and whether analog or digital channels are desired.

    Parameters:
    -----------
    meta : dict
        Metadata dictionary containing recording information.
    analog : bool, optional
        If True, return index for analog channels. If False, return index for digital channels. Default is True.

    Returns:
    --------
    slice
        A slice object representing the channel index range.

    Raises:
    -------
    ValueError
        If the recording type is not recognized.
    """
    if meta['typeThis'] == 'imec':
        n_ap = meta['snsApLfSy']['AP']
        n_lf = meta['snsApLfSy']['LF']
        n_sy = meta['snsApLfSy']['SY']
        if analog:
            channel_idx = slice(0, n_ap + n_lf)
        else:
            channel_idx = slice(n_ap + n_lf, n_ap + n_lf + n_sy)
    elif meta['typeThis'] == 'nidq':
        n_mn = meta['snsMnMaXaDw']['MN']
        n_ma = meta['snsMnMaXaDw']['MA']
        n_xa = meta['snsMnMaXaDw']['XA']
        n_dw = meta['snsMnMaXaDw']['DW']
        if analog:
            channel_idx = slice(0, n_mn + n_ma + n_xa)
        else:
            channel_idx = slice(n_mn + n_ma + n_xa, n_mn + n_ma + n_xa + n_dw)
    else:
        raise ValueError(f"Unrecognized recording type: {meta['typeThis']}")
    
    return channel_idx

def plot_chanmap(filename):
    """
    Plot the channel locations based on the parsed snsGeomMap data.
    
    Parameters:
    -----------
    filename : str
        Path to the meta file containing snsGeomMap data.
    """

    meta = read_meta(filename)
    if 'snsGeomMap' not in meta:
        raise ValueError("snsGeomMap not found in meta data")
    
    geom_data = meta['snsGeomMap']
    fig, ax = plt.subplots(figsize=(4, 8))
    
    x = [e['x'] + e['shank'] * geom_data['header']['shank_spacing'] for e in geom_data['electrodes']]
    z = [e['z'] for e in geom_data['electrodes']]
    used = [e['used'] for e in geom_data['electrodes']]
    channel_id = range(len(geom_data['electrodes']))
    
    ax.scatter(x, z, c=['blue' if u else 'red' for u in used], alpha=0.6, marker='s')
    
    for i, txt in enumerate(channel_id):
        ax.annotate(txt, (x[i], z[i]), xytext=(3, -2), textcoords='offset points', fontsize=8)
    
    ax.set_xlabel('X position (µm)')
    ax.set_ylabel('Z position (µm)')
    ax.set_title(f"Channel map for {geom_data['header']['part_number']}")
    ax.legend(['Used', 'Unused'], loc='upper right')
    
    plt.tight_layout()
    plt.show()


if __name__ == '__main__':
    fd = os.path.join(os.path.expanduser('~'), 'Dropbox (HHMI)', 'data')
    fn = finder(fd)
    # meta = read_meta(fn)
    # data = read_digital(fn)
    data = read_analog(fn, sample_range=(0, 3000))
    
    # denoise data
    # subtract median of each channel
    data -= np.median(data, axis=0, keepdims=True)
    # subtract median of each time sample from all channels (do not use for LFP data)
    data -= np.median(data, axis=1, keepdims=True)

    plt.imshow(data.T, aspect='auto', interpolation='none')
    plt.colorbar()
    plt.title('Raw Data')
    plt.xlabel('Time (samples)')
    plt.ylabel('Channel')
    plt.show()