#!/usr/bin/env python3

from __future__ import annotations
from abc import ABC, abstractmethod
import itertools
import random

from logic import *
from util import *
from proofrules import *
from parser import parse, fmla_parse
from verifier import verify

class Tactic(ABC):

    @abstractmethod
    def apply(self, seq: Sequent) -> set[Proof]:
        return set([seq])

class InstantiateForallTactic(Tactic):

    """
    A general tactic for proving sequents with a quantified
    formula in the context. The constructor takes a set of
    objects to instantiate the quantified variable with, and
    for each object `e`, `apply` returns a proof with one application
    of `@L` where the quantified variable is replaced with `e`
    in the context of the premise.
    """
    
    def __init__(self, grounds: set[Formula|Agent|Resource|Key]):
        self.grounds = grounds

    def apply(self, seq: Sequent) -> set[Proof]:
        pfs = set([])
        # seq is p1, ..., pn |- delta
        for p in seq.gamma:
            if not isinstance(p.p, Forall):
                continue
            # p is Proposition(@x . q)
            x = p.p.x
            q = p.p.p
            for e in self.grounds:
                # A new assumption to add to the context of the premise
                # by substituting e for x.
                new_assume = Proposition(apply_formula(q, {x: e}))
                # If this assumption is already in the context, don't bother
                # generating a proof
                if new_assume not in seq.gamma:
                    # The context for the premise of the proof that will be added
                    # contains the new assumption, and removes the @x . p judgement
                    # to avoid repeating the same step in the future.
                    new_gamma = [r for r in seq.gamma if r != p] + [new_assume]
                    # Before adding the proof, need to check whether delta is a
                    # truth (proposition) judgement or affirmation.
                    # This determines whether to use the "normal" @L rule, or the
                    # version that matches affirmation judgements.
                    which_rule = forallLeftRule if isinstance(seq.delta, Proposition) else forallLeftAffRule
                    # Add the proof to the return set.
                    pfs |= set([Proof([Sequent(new_gamma, seq.delta)], seq, which_rule)])
        return pfs

class CertTactic(Tactic):
    """
    A tactic for incorporating a says(iskey(#x #k)) formula into
    assumptions as a `iskey(#x, #k)` formula. 

    The premise `T.0` will be a closed proof of
    `isCA(#ca)` and 'iskey(#x, #k)' if and only if:
        - The given public key is the correct key #kfor #x is given
        - The sequent that the tactic is applied to
          has an ca(#ca) predicate that associates the given #ca 
          with a recognized certificate authority.
    The premise `T.1` of the resulting proof will be a
    sequent (i.e., an open/unclosed premise) with a set
    of assumptions identical to those in the sequent that
    the tactic is applied to, but will also include
    `iskey(#x, #k)`.

    If the two conditions listed above are not true of
    the sequent that the tactic is applied to, then
    `apply` returns the empty set.

    The proofs returned by this tactic can be closed by
    combining with other tactics using `ThenTactic`, or
    by applying other tactics to `pf.premises[1].conclusion`,
    (assuming `pf` is the returned proof), which will contain
    the unfinished sequent with the new `says` in its
    assumption, and chaining the two proofs together with
    `chain`.
    """
    
    def __init__(self, ag: Agent, agk: Key, ca: Agent, cak: Key):
        self._iskey = App(Operator.ISKEY, 2, [ag, agk])
        self._isca = App(Operator.ISCA, 1, [ca])
        self._says = App(Operator.SAYS, 2, [ca, App(Operator.ISKEY, 2, [ag, agk])])

        self._reqs = [
            Proposition(self._isca),
            Proposition(self._says)
        ]
        # print("initcertself.says", self._says)

    def apply(self, seq: Sequent) -> set[Proof]:
        # make sure all of the required assumptions are present
        violating_requirements = [p for p in self._reqs if p not in seq.gamma]

        if not all(p in seq.gamma for p in self._reqs):
            return set([])
        # if the `isKey` formula is already in the sequent's
        # assumptions, then there is no need to introduce it
        # again
        if Proposition(self._iskey) in seq.gamma:
            return set([])
        # cutgoal is the formula that we want to prove in the
        # left premise of the `cut` appliction
        cutgoal = Sequent(seq.gamma, Proposition(self._iskey))
        pf_isca = get_one_proof(Sequent(seq.gamma, Proposition(self._isca)), RuleTactic(identityRule))
        pf_says = get_one_proof(Sequent(seq.gamma, Proposition(self._says)), RuleTactic(identityRule))

        pf_cutgoal = Proof([pf_isca, pf_says], cutgoal, certRule)
    
        new_gamma = (
            seq.gamma + 
            [Proposition(self._iskey)]
        )
        newgoal = Sequent(new_gamma, seq.delta)
        # We need to look at the delta (proof goal) of the given sequent
        # to determine whether to use the version of `cut` for truth
        # or affirmation judgements
        whichRule = cutRule if isinstance(seq.delta, Proposition) else affCutRule
        # Now put everything together and return the proof
        return set([Proof([pf_cutgoal, newgoal], seq, whichRule)])


class SignTactic(Tactic):

    """
    A tactic for incorporating a signed credential into
    assumptions as a `says` formula. The `says` formula
    obtained by applying the `Sign` rule to `cred` with
    the public key of `agent` is incorporated into the
    context of an application of `Cut`. So if this tactic
    were constructed as:
    
    SignTactic(parse('sign(open(#b, <r>), [k])'), Agent('#a'))
    
    And applied to the following sequent:
    
    iskey(#a, [k]), sign(open(#b, <r>), [k]) |- P

    Then it would yield the following proof.

                        T.0  T.1
    cut -------------------------------------------------
        iskey(#a, [k]), sign(open(#b, <r>), [k]) |- P

    The premise `T.0` will be a closed proof of
    `#a says open(#b, <r>)` if and only if:
        - The `cred` argument is in the context of the
          sequent that the tactic is applied to.
        - The sequent that the tactic is applied to
          has an `iskey` predicate that associates the
          `agent` argument with the key appearing in
          the `cred` argument, i.e. `iskey(#a, [k])` in
          this example.
    The premise `T.1` of the resulting proof will be a
    sequent (i.e., an open/unclosed premise) with a set
    of assumptions identical to those in the sequent that
    the tactic is applied to, but will also include
    `#a says open(#b, <r>)`. I.e.:

    #a says open(#b, <r>), iskey(#a, [k]), sign(open(#b, <r>), [k]) |- P

    If the two conditions listed above are not true of
    the sequent that the tactic is applied to, then
    `apply` returns the empty set.

    The proofs returned by this tactic can be closed by
    combining with other tactics using `ThenTactic`, or
    by applying other tactics to `pf.premises[1].conclusion`,
    (assuming `pf` is the returned proof), which will contain
    the unfinished sequent with the new `says` in its
    assumption, and chaining the two proofs together with
    `chain`.
    """
    
    def __init__(self, cred: Formula, agent: Agent):
        self._cred = cred
        self._ag = agent
        # _says is the formula that we want to introduce in the cut
        self._says = App(Operator.SAYS, 2, [agent, cred.args[0]])
        # _iskey associates agent to the key in cred
        self._iskey = App(Operator.ISKEY, 2, [agent, cred.args[1]])
        # cred and _iskey need to be present in the sequent to
        # apply this tactic
        self._reqs = [
            Proposition(cred),
            Proposition(self._iskey)
        ]

    def apply(self, seq: Sequent) -> set[Proof]:
        # make sure all of the required assumptions are present
        violating_requirements = [p for p in self._reqs if p not in seq.gamma]

        if not all(p in seq.gamma for p in self._reqs):
            return set([])
        # if the `says` formula is already in the sequent's
        # assumptions, then there is no need to introduce it
        # again
        if Proposition(self._says) in seq.gamma:
            return set([])
        # cutgoal is the formula that we want to prove in the
        # left premise of the `cut` appliction
        cutgoal = Sequent(seq.gamma, Proposition(self._says))
        # `Sign` requires proving `_iskey` and `_cred`
        # We already checked that these are in the context,
        # so if we've gotten this far then we know that both
        # are proved with one application of the identity rule
        pf_iskey = get_one_proof(Sequent(seq.gamma, Proposition(self._iskey)), RuleTactic(identityRule))
        pf_cred = get_one_proof(Sequent(seq.gamma, Proposition(self._cred)), RuleTactic(identityRule))
        # The left premise of the cut is proved by combining these proofs
        # using the `Sign` rule
        pf_cutgoal = Proof([pf_iskey, pf_cred], cutgoal, signRule)
        # The right premise of the cut will copy the assumptions
        # in the current sequent, and add _says
        new_gamma = (
            seq.gamma + 
            [Proposition(self._says)]
        )
        # print("S: new gamma\n", new_gamma)
        newgoal = Sequent(new_gamma, seq.delta)
        # We need to look at the delta (proof goal) of the given sequent
        # to determine whether to use the version of `cut` for truth
        # or affirmation judgements
        whichRule = cutRule if isinstance(seq.delta, Proposition) else affCutRule
        # Now put everything together and return the proof
        return set([Proof([pf_cutgoal, newgoal], seq, whichRule)])

class RuleTactic(Tactic):

    """
    A general tactic for applying the rules in `proofrules` to make
    single-step progress on a proof. This does not attempt to apply
    the quantifier rules, and will raise `ValueError` if the constructor
    is given such a rule.
    """
    
    def __init__(self, rule: Rule):
        match rule:
            case Rule(_, _, '@L')|Rule(_,_,'@R'):
                raise ValueError(f'RuleTactic cannot be applied to @L or @R')
        self._rule = rule

    def apply(self, seq: Sequent) -> set[Proof]:
        pfs = set([])
        # Attempt to unify the given sequent with the conclusion of the rule.
        rhos = list(matchs_sequent(self._rule.conclusion, seq, {}))
        # There may be more than one substitution that unifies the
        # sequent with the rule, i.e., more than one opportunity to
        # apply the rule to this sequent. This tactic will generate
        # proofs for all of them.
        for rho in rhos:
            # We want to remove any assumptions from the sequent that
            # were used to match the rule. This is a general heuristic
            # to avoid infinite applications of the same step when
            # the tactic is combined with repetitive tactics.
            rule_gamma = apply_sequent(self._rule.conclusion, rho).gamma
            red_gamma = [p for p in seq.gamma if p not in rule_gamma]
            # The premises of each proof are obtained by applying
            # the substitution rho to each rule premise, and adding
            # the assumptions from the goal sequent that were not
            # used to match with the rule.
            prems = [
                Sequent(
                    list(set(apply_sequent(prem, rho).gamma + red_gamma)), 
                    apply_sequent(prem, rho).delta
                ) 
                for prem in self._rule.premises
            ]
            # Add the proof to the return set, and carry on
            pfs |= set([Proof(prems, seq, self._rule)])
        return pfs

class ThenTactic(Tactic):

    """
    A combinator tactic to apply a sequence of tactics,
    chaining the proofs obtained by later tactics to any
    unclosed branches of proofs generated by earlier tactics.

    This can be used in two modes, depending on the value 
    of `pass_on` given to the constructor. If `pass_on` is 
    `True`, then if the first tactic in the sequence fails
    to produce any proofs, then the next tactic is applied
    to the original sequent. If `pass_on` is `False`, then
    when the first tactic produces no proofs, no further
    tactics are applied.

    Most applications of this tactic will want to use it with
    `pass_on` set to `True`, so this is the default value.
    """
    
    def __init__(self, ts: list[Tactic], pass_on=True):
        self._ts = ts
        self._pass = pass_on

    def apply(self, seq: Sequent) -> set[Proof]:
        pfs = set([])
        # This tactic calls itself recursively, and
        # will terminate when the sequence of tactics
        # to apply is empty.
        if len(self._ts) > 0:
            # The first tactic in the sequence is applied directly,
            # and the remaining are dealt with recursively.
            t1, t2 = self._ts[0], ThenTactic(self._ts[1:], pass_on=self._pass)
            t1_pfs = t1.apply(seq)
            # If the first tactic didn't yield any proofs, then
            # return an empty set if `pass_on` is `False`.
            # Otherwise, just proceed to the next tactic
            # with the original sequent.
            if len(t1_pfs) == 0:
                return t2.apply(seq) if self._pass else set([])
            else:
                for pf1 in t1_pfs:
                    # For each proof returned by the first tactic,
                    # find the set of remaining unclosed branches
                    # (i.e. "obligations") by calling verify.
                    obs = [ob for ob in verify(pf1) if ob != seq]
                    # If all of the branches are closed, then
                    # simply return this proof.
                    # No future tactics will be able to make further
                    # progress on it.
                    if len(obs) == 0:
                        return set([pf1])
                    # Generate proofs for the remaining obligations
                    # by applying the rest of the tactic sequence
                    # to them
                    t2_pfs = [(ob, t2.apply(ob)) for ob in obs]
                    # Now we have a *set* of proofs for each unclosed
                    # branch. We don't know a priori which of them
                    # will be able to close, so we return proofs that
                    # try every combination of proof for all premises.
                    combs = list(
                        itertools.product(
                            *[pf if len(pf) > 0 else [ob] for ob, pf in t2_pfs]
                        )
                    )
                    # The list of combinations can be empty if there were
                    # no proofs for one of the obligations.
                    if len(combs) > 0:
                        for comb in combs:
                            # If this isn't the case, then chain each combination
                            # of obligation proofs onto the current proof,
                            # and add it to the return set.
                            chains = {ob: comb[i] for i, ob in enumerate(obs)}
                            pfs |= set([chain(pf1, chains)])
                    else:
                        # If this happens, then just add the current proof.
                        pfs |= set([pf1])
        return pfs

class OrElseTactic(Tactic):

    """
    Apply a sequence of tactics until progress is made.
    Specifically, continue applying tactics in the
    given sequence while they fail to produce any proofs.
    When a tactic does produce proofs, return them and
    stop applying further tactics.
    """
    
    def __init__(self, ts: list[Tactic]):
        self._ts = ts

    def apply(self, seq: Sequent) -> set[Proof]:
        # This works in a similar way to ThenTactic,
        # making recursive calls to itself
        # for as long as tactics attempted so far do not
        # produce any proofs.
        if len(self._ts) > 0:
            t_pfs = self._ts[0].apply(seq)
            if len(t_pfs) == 0:
                return OrElseTactic(self._ts[1:]).apply(seq)
            else:
                return t_pfs
        return set([])

def chain(pf: Proof, chains: dict[Sequent, Proof]) -> Proof:
    """
    Chain proofs for unclosed branches of a proof into
    the original proof. An unclosed branch in a proof
    will manifest as a `Sequent` object in a premise,
    rather than a `Proof` object. The `chains` argument
    maps these sequents to proofs, which are substituted
    into the given proof `pf`.
    
    Args:
        pf (Proof): A proof, potentially containing unclosed
            branches.
        chains (dict[Sequent, Proof]): Mapping of unfinished
            branches to their proofs.
    
    Returns:
        Proof: The proof described in the summary.
    """
    # If the mapping contains a proof for the original
    # conclusion, then return it.
    if pf.conclusion in chains:
        return chains[pf.conclusion]
    new_prems = []
    # Look for unfinished branches among the premises.
    for prem in pf.premises:
        if isinstance(prem, Proof):
            # If a premise already has a proof, it may still
            # contain unfinished branches. Recurse on it.
            new_prems.append(chain(prem, chains))    
        elif prem in chains:
            # Otherwise, if the premise is a sequent mapped to
            # a proof by the given `chains`, then use the mapping
            new_prems.append(chains[prem] if chains[prem] is not None else prem)
        else:
            # Otherwise, it is a sequent that the mapping does
            # not have a proof for. Leave it unchanged.
            new_prems.append(prem)
    return Proof(new_prems, pf.conclusion, pf.rule)

def get_one_proof(seq: Sequent, t: Tactic) -> Optional[Proof]:
    """
    Convenience function to look for a closed proof
    in the set returned by a tactic.
    
    Args:
        seq (Sequent): A sequent to apply the tactic to.
        t (Tactic): Tactic to apply.
    
    Returns:
        Optional[Proof]: If the tactic yields a closed proof,
            i.e., one for which `verify` returns an empty set
            of obligations, then that proof is returned.
            Otherwise, `None`.
    """
    for pf in t.apply(seq):
        if len(verify(pf)) == 0:
            return pf
    return None

def prove(seq: Sequent) -> Optional[Proof]:
    """
    Produce a proof for a given sequent, if the
    student's tactic is able to find one. You
    should implement this function by developing
    one or more tactics for the authorization
    policies specified in the lab, and applying them
    to `seq`.
    
    Args:
        seq (Sequent): Sequent to prove.
    
    Returns:
        Optional[Proof]: A closed proof of `seq`, if
            one exists. Otherwise `None`.
    """
    
    # root says open(#kevinfan, <kevinfan.txt>) proof
    t = ThenTactic([
        SignTactic(parse('sign(iskey(#root, [2b:8f:e8:9b:8b:76:37:a7:3b:7e:85:49:9d:87:7b:3b]), [43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f])'), Agent('#ca')),
        CertTactic(Agent('#root'), Key('[2b:8f:e8:9b:8b:76:37:a7:3b:7e:85:49:9d:87:7b:3b]'), Agent('#ca'), Key('[43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f]')),
        SignTactic(parse('sign((open(#kevinfan, <kevinfan.txt>)), [2b:8f:e8:9b:8b:76:37:a7:3b:7e:85:49:9d:87:7b:3b])'), Agent('#root')),
        RuleTactic(identityRule)
    ])

    # root says open(#kevinfan, <shared.txt>) proof
    t2 = ThenTactic([
        SignTactic(parse('sign((iskey(#mfredrik, [d3:c6:2a:1b:63:20:f9:75:fd:1b:ec:fc:ad:19:f1:47])), [43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f])'), Agent('#ca')),
        CertTactic(Agent('#mfredrik'), Key('[d3:c6:2a:1b:63:20:f9:75:fd:1b:ec:fc:ad:19:f1:47]'), Agent('#ca'), Key('[43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f]')),
        SignTactic(parse('sign((iskey(#root, [2b:8f:e8:9b:8b:76:37:a7:3b:7e:85:49:9d:87:7b:3b])), [43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f])'), Agent('#ca')),
        CertTactic(Agent('#root'), Key('[2b:8f:e8:9b:8b:76:37:a7:3b:7e:85:49:9d:87:7b:3b]'), Agent('#ca'), Key('[43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f]')),
        SignTactic(parse('sign((@A . (((#mfredrik says open(A, <shared.txt>)) -> open(A, <shared.txt>)))), [2b:8f:e8:9b:8b:76:37:a7:3b:7e:85:49:9d:87:7b:3b])'), Agent('#root')),
        RuleTactic(saysLeftRule),
        RuleTactic(saysRightRule),
        RuleTactic(saysLeftRule),
        InstantiateForallTactic([Agent('#kevinfan')]),
        RuleTactic(impLeftAffRule),
        SignTactic(parse('sign(open(#kevinfan, <shared.txt>), [d3:c6:2a:1b:63:20:f9:75:fd:1b:ec:fc:ad:19:f1:47])'), Agent('#mfredrik')),
        RuleTactic(saysLeftRule),
        RuleTactic(affRule),
        RuleTactic(identityRule)
    ])

    # root says open(#kevinfan, <secret.txt>) proof
    t3 = ThenTactic([            
        SignTactic(parse('sign(iskey(#justinyo, [d2:15:e7:01:be:ef:fa:e6:08:ca:6c:bd:90:f4:1b:af]), [43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f])'), Agent('#ca')),
        CertTactic(Agent('#justinyo'), Key('[d2:15:e7:01:be:ef:fa:e6:08:ca:6c:bd:90:f4:1b:af]'), Agent('#ca'), Key('[43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f]')),
        SignTactic(parse('sign(iskey(#mdhamank, [71:14:55:85:b1:59:ae:76:b2:56:4b:36:09:01:f5:3f]), [43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f])'), Agent('#ca')),
        CertTactic(Agent('#mdhamank'), Key('[71:14:55:85:b1:59:ae:76:b2:56:4b:36:09:01:f5:3f]'), Agent('#ca'), Key('[43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f]')),          
        SignTactic(parse('sign(iskey(#mfredrik, [d3:c6:2a:1b:63:20:f9:75:fd:1b:ec:fc:ad:19:f1:47]), [43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f])'), Agent('#ca')),
        CertTactic(Agent('#mfredrik'), Key('[d3:c6:2a:1b:63:20:f9:75:fd:1b:ec:fc:ad:19:f1:47]'), Agent('#ca'), Key('[43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f]')),
        SignTactic(parse('sign(iskey(#dsduena, [db:b2:b9:b7:01:83:9c:3e:7d:ac:c4:87:00:cd:79:2f]), [43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f])'), Agent('#ca')),
        CertTactic(Agent('#dsduena'), Key('[db:b2:b9:b7:01:83:9c:3e:7d:ac:c4:87:00:cd:79:2f]'), Agent('#ca'), Key('[43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f]')),
        SignTactic(parse('sign(iskey(#root, [2b:8f:e8:9b:8b:76:37:a7:3b:7e:85:49:9d:87:7b:3b]), [43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f])'), Agent('#ca')),
        CertTactic(Agent('#root'), Key('[2b:8f:e8:9b:8b:76:37:a7:3b:7e:85:49:9d:87:7b:3b]'), Agent('#ca'), Key('[43:c9:43:e6:28:37:ec:23:1a:bc:83:c6:eb:87:e8:6f]')),
        SignTactic(parse('sign((open(#mfredrik, <secret.txt>)), [2b:8f:e8:9b:8b:76:37:a7:3b:7e:85:49:9d:87:7b:3b])'), Agent('#root')),
        SignTactic(parse('sign((open(#dsduena, <secret.txt>)), [d3:c6:2a:1b:63:20:f9:75:fd:1b:ec:fc:ad:19:f1:47])'), Agent('#mfredrik')),
        SignTactic(parse('sign((open(#justinyo, <secret.txt>)), [db:b2:b9:b7:01:83:9c:3e:7d:ac:c4:87:00:cd:79:2f])'), Agent('#dsduena')),
        SignTactic(parse('sign((open(#mdhamank, <secret.txt>)), [d2:15:e7:01:be:ef:fa:e6:08:ca:6c:bd:90:f4:1b:af])'), Agent('#justinyo')), 
        SignTactic(parse('sign((open(#kevinfan, <secret.txt>)), [71:14:55:85:b1:59:ae:76:b2:56:4b:36:09:01:f5:3f])'), Agent('#mdhamank')),
        SignTactic(parse('sign(((@A . ((@R . ((open(A, R) -> (@B . (((A says open(B, R)) -> open(B, R)))))))))), [2b:8f:e8:9b:8b:76:37:a7:3b:7e:85:49:9d:87:7b:3b])'), Agent('#root')),
        RuleTactic(saysRightRule),
        RuleTactic(saysLeftRule),
        RuleTactic(saysLeftRule),
        InstantiateForallTactic([Agent('#mfredrik')]),
        InstantiateForallTactic([Resource('<secret.txt>')]),
        RuleTactic(impLeftAffRule),
        InstantiateForallTactic([Agent('#dsduena')]),
        RuleTactic(impLeftAffRule),
        SignTactic(parse('sign(((@A . ((@R . ((open(A, R) -> (@B . (((A says open(B, R)) -> open(B, R)))))))))), [2b:8f:e8:9b:8b:76:37:a7:3b:7e:85:49:9d:87:7b:3b])'), Agent('#root')),
        RuleTactic(saysLeftRule),
        InstantiateForallTactic([Agent('#dsduena')]),
        InstantiateForallTactic([Resource('<secret.txt>')]),
        RuleTactic(impLeftAffRule),
        InstantiateForallTactic([Agent('#justinyo')]),
        RuleTactic(impLeftAffRule),
        SignTactic(parse('sign(((@A . ((@R . ((open(A, R) -> (@B . (((A says open(B, R)) -> open(B, R)))))))))), [2b:8f:e8:9b:8b:76:37:a7:3b:7e:85:49:9d:87:7b:3b])'), Agent('#root')),
        RuleTactic(saysLeftRule),
        InstantiateForallTactic([Agent('#justinyo')]),
        InstantiateForallTactic([Resource('<secret.txt>')]),
        RuleTactic(impLeftAffRule),
        InstantiateForallTactic([Agent('#mdhamank')]),
        RuleTactic(impLeftAffRule),
        SignTactic(parse('sign(((@A . ((@R . ((open(A, R) -> (@B . (((A says open(B, R)) -> open(B, R)))))))))), [2b:8f:e8:9b:8b:76:37:a7:3b:7e:85:49:9d:87:7b:3b])'), Agent('#root')),
        RuleTactic(saysLeftRule),
        InstantiateForallTactic([Agent('#mdhamank')]),
        InstantiateForallTactic([Resource('<secret.txt>')]),
        RuleTactic(impLeftAffRule),
        InstantiateForallTactic([Agent('#kevinfan')]),
        RuleTactic(impLeftAffRule),
        RuleTactic(affRule),
        RuleTactic(identityRule)
    ])

    # exploit proof:
    t4 = ThenTactic([
        SignTactic(parse('sign((iskey(#root, [9e:8e:36:8c:6d:26:1d:e3:65:8e:b5:39:36:ea:c5:22])), [e7:5c:3f:40:d3:2c:8b:ea:98:f6:f1:ba:98:53:4e:86])'), Agent('#exploiter')),
        CertTactic(Agent('#root'), Key('[9e:8e:36:8c:6d:26:1d:e3:65:8e:b5:39:36:ea:c5:22]'), Agent('#exploiter'), Key('[e7:5c:3f:40:d3:2c:8b:ea:98:f6:f1:ba:98:53:4e:86]')),
        SignTactic(parse('sign((open(#kevinfan, <bigsecret.txt>)), [9e:8e:36:8c:6d:26:1d:e3:65:8e:b5:39:36:ea:c5:22])'), Agent('#root')),
        RuleTactic(saysRightRule),
        RuleTactic(saysLeftRule),
        RuleTactic(affRule),
        RuleTactic(identityRule)
    ])

    #for pf in t4.apply(seq):
    #    print(stringify(pf, 500, None, False))

    if get_one_proof(seq,t4) != None:
        return get_one_proof(seq,t4)
    elif get_one_proof(seq,t3) != None:
        return get_one_proof(seq,t3)
    elif get_one_proof(seq,t2) != None:
        return get_one_proof(seq,t2)
    else:
       return get_one_proof(seq, t)

if __name__ == '__main__':
    seq = parse('#root says open(#kevinfan, <bigsecret.txt>) |- #root says open(#kevinfan, <bigsecret.txt>)')
    tx = RuleTactic(identityRule)

    for pf in tx.apply(seq):
        print(stringify(pf, 500, None, True))

    print(tx.apply(seq))
    pass
