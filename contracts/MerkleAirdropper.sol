// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.7.6;

interface IERC20 {
    function transferFrom(address sender, address recipient, uint256 amount) external returns (bool);
}


// MerkleAirdropper for ongoing FLTY airdrop to LQTY holders & stakers
// Based on the EMN refund contract by banteg - https://github.com/banteg/your-eminence
contract MerkleAirdropper {

    // 2-year airdrop process
    uint256 constant ROUNDS = 104;

    bytes32[] public merkleRoots;
    uint256 public lastRoot;

    // admin address for proposing a new merkle root
    address public authority;
    // ERC20 token for distributing
    address public airdropToken;

    modifier authorityOnly {
      require(msg.sender == authority, "MerkleAirdropper: Authority only.");
      _;
    }

    event Claimed(
        uint256 merkleIndex,
        uint256 index,
        address account,
        uint256 amount
    );

    // This is a packed array of booleans.
    mapping(uint256 => mapping(uint256 => uint256)) private claimedBitMap;

    constructor(address _authority, address _airdropToken) {
        authority = _authority;
        airdropToken = _airdropToken;
    }

    function setAuthority(address _authority) public authorityOnly {
        authority = _authority;
    }

    // Each week, the authority calls to submit the merkle root for a new airdrop.
    function proposewMerkleRoot(bytes32 _merkleRoot) public authorityOnly {
        require(merkleRoots.length < ROUNDS);
        require(block.timestamp > lastRoot + 604800);
        merkleRoots.push(_merkleRoot);
        lastRoot = block.timestamp / 604800 * 604800;
    }

    function isClaimed(uint256 merkleIndex, uint256 index) public view returns (bool) {
        uint256 claimedWordIndex = index / 256;
        uint256 claimedBitIndex = index % 256;
        uint256 claimedWord = claimedBitMap[merkleIndex][claimedWordIndex];
        uint256 mask = (1 << claimedBitIndex);
        return claimedWord & mask == mask;
    }

    function _setClaimed(uint256 merkleIndex, uint256 index) private {
        uint256 claimedWordIndex = index / 256;
        uint256 claimedBitIndex = index % 256;
        claimedBitMap[merkleIndex][claimedWordIndex] = claimedBitMap[merkleIndex][claimedWordIndex] | (1 << claimedBitIndex);
    }

    function claim(uint256 merkleIndex, uint256 index, uint256 amount, bytes32[] calldata merkleProof) external {
        require(merkleIndex < merkleRoots.length, "MerkleAirdropper: Invalid merkleIndex");
        require(!isClaimed(merkleIndex, index), 'MerkleAirdropper: Drop already claimed.');

        // Verify the merkle proof.
        bytes32 node = keccak256(abi.encodePacked(index, msg.sender, amount));
        require(verify(merkleProof, merkleRoots[merkleIndex], node), 'MerkleAirdropper: Invalid proof.');

        // Mark it claimed and send the token.
        _setClaimed(merkleIndex, index);
        IERC20(airdropToken).transferFrom(authority, msg.sender, amount);

        emit Claimed(merkleIndex, index, msg.sender, amount);
    }

    function verify(bytes32[] calldata proof, bytes32 root, bytes32 leaf) internal pure returns (bool) {
        bytes32 computedHash = leaf;

        for (uint256 i = 0; i < proof.length; i++) {
            bytes32 proofElement = proof[i];

            if (computedHash <= proofElement) {
                // Hash(current computed hash + current element of the proof)
                computedHash = keccak256(abi.encodePacked(computedHash, proofElement));
            } else {
                // Hash(current element of the proof + current computed hash)
                computedHash = keccak256(abi.encodePacked(proofElement, computedHash));
            }
        }

        // Check if the computed hash (root) is equal to the provided root
        return computedHash == root;
    }

}
