from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable

from app.ledger.canonical import canonical_json


EMPTY_ROOT = sha256(b"").hexdigest()


def hash_leaf_payload(payload: dict) -> str:
    return sha256(canonical_json(payload)).hexdigest()


def hash_pair(left_hex: str, right_hex: str) -> str:
    return sha256(bytes.fromhex(left_hex) + bytes.fromhex(right_hex)).hexdigest()


@dataclass(frozen=True)
class ProofNode:
    direction: str  # "left" means sibling is on left side
    hash: str


class MerkleTree:
    def __init__(self, leaf_hashes: Iterable[str]):
        leaves = list(leaf_hashes)
        self.leaf_hashes = leaves
        self.levels: list[list[str]] = []
        if not leaves:
            self.levels = [[EMPTY_ROOT]]
            return

        current = leaves[:]
        self.levels.append(current)
        while len(current) > 1:
            if len(current) % 2 == 1:
                current = current + [current[-1]]
            nxt: list[str] = []
            for i in range(0, len(current), 2):
                nxt.append(hash_pair(current[i], current[i + 1]))
            self.levels.append(nxt)
            current = nxt

    @property
    def root(self) -> str:
        return self.levels[-1][0]

    def proof(self, index: int) -> list[ProofNode]:
        if not self.leaf_hashes:
            raise IndexError("cannot build proof from empty tree")
        if index < 0 or index >= len(self.leaf_hashes):
            raise IndexError("leaf index out of range")

        proof: list[ProofNode] = []
        idx = index
        for level_idx in range(len(self.levels) - 1):
            level = self.levels[level_idx]
            padded = level if len(level) % 2 == 0 else level + [level[-1]]
            is_right = idx % 2 == 1
            sibling_index = idx - 1 if is_right else idx + 1
            sibling_hash = padded[sibling_index]
            direction = "left" if is_right else "right"
            proof.append(ProofNode(direction=direction, hash=sibling_hash))
            idx = idx // 2
        return proof


def verify_proof(leaf_hash: str, proof: list[dict] | list[ProofNode], root: str) -> bool:
    current = leaf_hash
    for node in proof:
        direction = node.direction if isinstance(node, ProofNode) else node["direction"]
        sibling_hash = node.hash if isinstance(node, ProofNode) else node["hash"]
        if direction == "left":
            current = hash_pair(sibling_hash, current)
        elif direction == "right":
            current = hash_pair(current, sibling_hash)
        else:
            return False
    return current == root
