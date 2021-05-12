from brownie import Contract, chain, web3
import json
from pathlib import Path
import time
from itertools import zip_longest
from collections import defaultdict, deque
from fractions import Fraction
from eth_abi.packed import encode_abi_packed
from eth_utils import encode_hex

lqty_total_supply = 100000000000000000000000000

class MerkleTree:
    def __init__(self, elements):
        self.elements = sorted(set(web3.keccak(hexstr=el) for el in elements))
        self.layers = MerkleTree.get_layers(self.elements)

    @property
    def root(self):
        return self.layers[-1][0]

    def get_proof(self, el):
        el = web3.keccak(hexstr=el)
        idx = self.elements.index(el)
        proof = []
        for layer in self.layers:
            pair_idx = idx + 1 if idx % 2 == 0 else idx - 1
            if pair_idx < len(layer):
                proof.append(encode_hex(layer[pair_idx]))
            idx //= 2
        return proof

    @staticmethod
    def get_layers(elements):
        layers = [elements]
        while len(layers[-1]) > 1:
            layers.append(MerkleTree.get_next_layer(layers[-1]))
        return layers

    @staticmethod
    def get_next_layer(elements):
        return [MerkleTree.combined_hash(a, b) for a, b in zip_longest(elements[::2], elements[1::2])]

    @staticmethod
    def combined_hash(a, b):
        if a is None:
            return b
        if b is None:
            return a
        return web3.keccak(b''.join(sorted([a, b])))


def get_lqty_addresses(addresses, start_block, snapshot_block):
    lqty_contract = Contract('0x6dea81c8171d0ba574754ef6f8b412f2ed88c54d')
    lqty = web3.eth.contract(lqty_contract.address, abi=lqty_contract.abi)

    addresses = set(addresses)
    new_addresses = set()
    latest = chain[-1].number
    for height in range(start_block, latest+1, 10000):
        print(f"{height}/{latest}")
        for i in lqty.events.Transfer().getLogs(fromBlock=height, toBlock=min(height+10000, latest)):
            if i.args.value == 0:
                continue
            for addr in [i.args["from"], i.args.to]:
                if addr not in addresses:
                    new_addresses.add(addr)
    # filter out contract in new addresses
    address_check_contract = Contract('0xd24a0C1C6f646BA7152f0F028802C36EA964e387')
    mc_data = [
        [str(address_check_contract), address_check_contract.isContract.encode_input(addr)]
        for addr in new_addresses
    ]
    multicall = Contract('0x5e227AD1969Ea493B43F840cfF78d08a6fc17796')

    new_addresses = list(new_addresses)
    step = 1000
    for i in range(0, len(mc_data), step):
        print(f"{i}/{len(mc_data)}")
        # may have false negatives on those selfdestructed contracts but should be fine
        response = multicall.aggregate.call(mc_data[i:i+step], block_identifier=snapshot_block)[1]
        decoded = [address_check_contract.isContract.decode_output(data) for data in response]
        for addr, is_contract in zip(new_addresses[i:i+step], decoded):
            if not is_contract:
                addresses.add(addr)

    print(f"\nFound {len(addresses)} addresses!")
    return sorted(addresses), latest


def get_block_at_timestamp(timestamp):
    current = chain[-1]

    high = current.number - (current.timestamp - timestamp) // 15
    low = current.number - (current.timestamp - timestamp) // 11

    while low <= high:
        middle = low + (high - low) // 2
        block = chain[middle]
        if block.timestamp >= timestamp and chain[middle-1].timestamp < timestamp:
            return middle
        elif block.timestamp < timestamp:
            low = middle + 1
        else:
            high = middle - 1
    raise ValueError


def get_lqty_holder_balances(addresses, snapshot_block):
    lqty_contract = Contract('0x6dea81c8171d0ba574754ef6f8b412f2ed88c54d')
    mc_data = [[str(lqty_contract), lqty_contract.balanceOf.encode_input(addr)] for addr in addresses]
    multicall = Contract('0x5e227AD1969Ea493B43F840cfF78d08a6fc17796')

    balances = {}
    step = 1000
    for i in range(0, len(mc_data), step):
        print(f"{i}/{len(mc_data)}")
        response = multicall.aggregate.call(mc_data[i:i+step], block_identifier=snapshot_block)[1]
        decoded = [lqty_contract.balanceOf.decode_output(data) for data in response]
        balances.update({addr.lower(): balance for addr, balance in zip(addresses[i:i+step], decoded)})
    print("total LQTY holder balance:", sum(balances.values()))
    return balances


def get_lqty_staker_balances(addresses, snapshot_block):
    staking_contract = Contract('0x4f9Fbb3f1E99B56e0Fe2892e623Ed36A76Fc605d')
    mc_data = [[str(staking_contract), staking_contract.stakes.encode_input(addr)] for addr in addresses]
    multicall = Contract('0x5e227AD1969Ea493B43F840cfF78d08a6fc17796')

    balances = {}
    step = 1000
    for i in range(0, len(mc_data), step):
        print(f"{i}/{len(mc_data)}")
        response = multicall.aggregate.call(mc_data[i:i+step], block_identifier=snapshot_block)[1]
        decoded = [staking_contract.stakes.decode_output(data) for data in response]
        balances.update({addr.lower(): balance for addr, balance in zip(addresses[i:i+step], decoded)})
    print("total LQTY staker balance:", sum(balances.values()))
    return balances


def get_proof(balances, snapshot_block):
    total_distribution = (250000000*10**18)//104
    total_lqty = sum(balances.values())
    balances = {k: int(Fraction(v*total_distribution, total_lqty)) for k, v in balances.items()}
    balances = {k: v for k, v in balances.items() if v}

    # handle any rounding errors
    addresses = deque(balances)
    while sum(balances.values()) < total_distribution:
        balances[addresses[0]] += 1
        addresses.rotate()

    assert sum(balances.values()) == total_distribution

    elements = [(index, account, balances[account]) for index, account in enumerate(sorted(balances))]
    nodes = [encode_hex(encode_abi_packed(['uint', 'address', 'uint'], el)) for el in elements]
    tree = MerkleTree(nodes)
    distribution = {
        'merkleRoot': encode_hex(tree.root),
        'tokenTotal': hex(sum(balances.values())),
        'blockHeight': snapshot_block,
        'claims': {
            user: {'index': index, 'amount': hex(amount), 'proof': tree.get_proof(nodes[index])}
            for index, user, amount in elements
        },
    }
    print(f'merkle root: {encode_hex(tree.root)}')
    return distribution


def main():
    address_json = Path('addresses.json')
    if address_json.exists():
        with address_json.open() as fp:
            data = json.load(fp)
            start_block = data['latest']
            addresses = data['addresses']
    else:
        start_block = 12178618  # LQTY contract created at this block
        addresses = []

    snapshot_time = int((time.time() // 604800) * 604800)
    snapshot_block = get_block_at_timestamp(snapshot_time)

    addresses, height = get_lqty_addresses(addresses, start_block, snapshot_block)
    with address_json.open('w') as fp:
        json.dump({'addresses': addresses, 'latest': height}, fp)

    holder_balances = get_lqty_holder_balances(addresses, snapshot_block)
    staker_balances = get_lqty_staker_balances(addresses, snapshot_block)
    # merge both balances
    balances = defaultdict(int)
    for d in [holder_balances, staker_balances]:
        for k, v in d.items():
            balances[k] += v
    distribution = get_proof(balances, snapshot_block)

    date = time.strftime("%Y-%m-%d", time.gmtime(snapshot_time))
    distro_json = Path(f'distributions/distribution-{date}.json')
    bal_json = Path(f'distributions/balance-{date}.json')

    with distro_json.open('w') as fp:
        json.dump(distribution, fp)
    print(f"Distribution saved to {distro_json}")

    def format_to_json(stake_bal, hold_bal, total_bal):
        output = {}
        for k, v in total_bal.items():
            if v == 0:
                continue
            output[k] = {
                'totalBalance': v,
                'stakingBalance': stake_bal.get(k, 0),
                'holdingBalance': hold_bal.get(k, 0)
            }
        return output

    with bal_json.open('w') as fp:
        json.dump(format_to_json(staker_balances, holder_balances, balances), fp)
    print(f"Balances saved to {bal_json}")
